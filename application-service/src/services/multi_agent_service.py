"""Multi-agent config management service.

Covers the definition-plane read and update paths of the monolith's
``MultiAgentService`` consumed by the multi-agent config endpoints, plus the
runtime entry points (run / run_stream) backing the draft-run surface. The
delete path remains in the monolith.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models import App, AppRelease
from ..models.multi_agent_model import MultiAgentConfig
from ..repositories import cross_domain_read
from ..repositories.multi_agent_repository import MultiAgentConfigRepository
from ..schemas.multi_agent_schema import MultiAgentConfigUpdate, MultiAgentRunRequest
from .model_ref_gate import raise_model_unavailable, resolve_invoke_ref
from .multi_agent_orchestrator import MultiAgentOrchestrator
from .multi_agent_release_resolver import describe_release_state, resolve_release_for_save

logger = logging.getLogger(__name__)


def convert_uuids_to_str(obj: Any) -> Any:
    """Recursively convert all UUIDs in an object to strings.

    Args:
        obj: Object to convert (dict, list, UUID, etc.)

    Returns:
        The converted object
    """
    if isinstance(obj, uuid.UUID):
        return str(obj)
    elif isinstance(obj, str):
        # PostgreSQL text/jsonb forbids NUL (U+0000). Node output (e.g. the raw
        # chunk text returned by knowledge retrieval) may contain this character;
        # storing it as-is raises UntranslatableCharacterError (22P05 unsupported
        # Unicode escape sequence). This function sanitizes values before write.
        return obj.replace("\x00", "") if "\x00" in obj else obj
    elif isinstance(obj, dict):
        return {k: convert_uuids_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuids_to_str(item) for item in obj]
    else:
        return obj


class MultiAgentService:
    """Multi-agent config management service."""

    # Maximum versions per round (including the original reply), aligned with
    # WorkflowService.MAX_REGENERATE_VERSIONS.
    MAX_REGENERATE_VERSIONS = 5

    def __init__(self, db: Session):
        self.db = db

    async def _get_last_current_assistant_id(self, conversation_id) -> uuid.UUID | None:
        """ID of the conversation's last is_current assistant message before this turn.

        Used as the parent_message_id of this turn's user message (the user message
        attaches to the version the user is currently viewing), so _load_branch_chain
        can isolate each version's follow-up turns precisely by parent instead of
        falling back to time windows. Must be called before this turn's messages are
        enqueued. Query failure degrades to None (= legacy behavior) and never blocks
        persistence.
        """
        if not conversation_id:
            return None
        try:
            from ..db import get_async_db_context
            from ..models import Message

            if isinstance(conversation_id, uuid.UUID):
                conv_uuid = conversation_id
            else:
                conv_uuid = uuid.UUID(str(conversation_id))
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(Message.id).where(
                        Message.conversation_id == conv_uuid,
                        Message.role == "assistant",
                        Message.is_current.is_(True),
                        Message.is_deleted.is_not(True),
                    ).order_by(Message.created_at.desc()).limit(1)
                )
                return result.scalar_one_or_none()
        except Exception as e:
            logger.warning(
                "查询上一条当前 assistant 消息失败，user 消息不记录 parent",
                extra={"error": str(e)},
            )
            return None

    async def _conversation_has_messages(self, conversation_id: uuid.UUID) -> bool:
        """New-conversation check (aligned with AppChatService._conversation_has_messages)."""
        from ..models import Message

        if isinstance(self.db, AsyncSession):
            result = await self.db.execute(
                select(Message.id)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.created_at)
                .limit(1)
            )
            return result.first() is not None
        from .conversation_service import ConversationService
        existing_messages = ConversationService(
            self.db
        ).message_repo.get_message_by_conversation_id(
            conversation_id=conversation_id,
            limit=1,
        )
        return len(existing_messages) > 0

    async def _resolve_cluster_opening(
        self,
        orchestrator: MultiAgentOrchestrator,
        conversation_id: uuid.UUID,
        variables: dict[str, Any] | None,
    ) -> tuple[str | None, list[str]]:
        """Cluster opening statement for a new conversation (trial-run entries).

        Queries the DB only when features.opening_statement is enabled. Backward
        compatible: existing clusters without features return (None, []) with no
        extra queries; failures degrade to no opening statement and never block
        the conversation.
        """
        features = getattr(orchestrator, "_features", None) or {}
        opening_cfg = features.get("opening_statement")
        if not (
            isinstance(opening_cfg, dict)
            and opening_cfg.get("enabled")
            and opening_cfg.get("statement")
        ):
            return None, []
        try:
            is_new_conversation = not await self._conversation_has_messages(conversation_id)
            from .cluster_chat_features import resolve_opening

            return resolve_opening(features, variables, is_new_conversation)
        except Exception as e:  # opening statements are an incremental capability
            logger.warning(f"集群开场白解析失败（已跳过）: {e}")
            return None, []

    def get_config(self, app_id: uuid.UUID) -> MultiAgentConfig | None:
        """Get the multi-agent config.

        Args:
            app_id: App ID

        Returns:
            The multi-agent config, or None if it does not exist
        """
        return MultiAgentConfigRepository(self.db).get_by_app_id(app_id)

    async def get_config_async(self, app_id: uuid.UUID) -> MultiAgentConfig | None:
        """Async variant of ``get_config`` (runtime plane); falls back to the
        sync read when ``self.db`` is a plain Session."""

        if isinstance(self.db, AsyncSession):
            result = await self.db.execute(
                select(MultiAgentConfig)
                .where(
                    MultiAgentConfig.app_id == app_id,
                    MultiAgentConfig.is_active.is_(True),
                )
                .order_by(MultiAgentConfig.updated_at.desc())
            )
            return result.scalars().first()
        return self.get_config(app_id)

    def get_multi_agent_configs(self, app_id: uuid.UUID) -> dict[str, Any] | None:
        """Get the latest effective multi-agent config by app_id, converting
        agent_id to app_id.

        Args:
            app_id: App ID

        Returns:
            The converted config dict, or None if it does not exist
        """
        config = self.get_config(app_id)
        if not config:
            return None

        # Compatibility code
        if not config.default_model_config_id:
            master_release = self.db.get(AppRelease, config.master_agent_id)
            config.default_model_config_id = (
                master_release.default_model_config_id if master_release else None
            )

        # Convert sub_agents agent_id (release_id) to app_id and expose
        # release-policy info: release_policy / release_id (pinned only) /
        # current_release_id / has_newer_release
        converted_sub_agents = []
        for sub_agent in config.sub_agents:
            sub_agent_copy = sub_agent.copy()
            try:
                state = describe_release_state(self.db, sub_agent)
                if state["app_id"]:
                    sub_agent_copy["agent_id"] = state["app_id"]
                sub_agent_copy["release_policy"] = state["release_policy"]
                sub_agent_copy["release_id"] = state["release_id"]
                sub_agent_copy["current_release_id"] = state["current_release_id"]
                sub_agent_copy["has_newer_release"] = state["has_newer_release"]
            except Exception as e:
                logger.warning(
                    f"转换 sub_agent agent_id 失败: {sub_agent.get('agent_id')}, 错误: {str(e)}"
                )
            converted_sub_agents.append(sub_agent_copy)

        # Build the returned config dict
        return {
            "id": config.id,
            "app_id": config.app_id,
            "default_model_config_id": config.default_model_config_id,
            "model_parameters": config.model_parameters,
            "orchestration_mode": config.orchestration_mode,
            "sub_agents": converted_sub_agents,
            "routing_rules": config.routing_rules,
            "execution_config": config.execution_config,
            # Supervisor-as-agent: the read-back must include this key, otherwise
            # the frontend getData cannot see it, and default backfill resets the
            # supervisor capability surface to all defaults (the toggle
            # "turns off after save")
            "supervisor_config": config.supervisor_config,
            "aggregation_strategy": config.aggregation_strategy,
            "is_active": config.is_active,
            "created_at": config.created_at,
            "updated_at": config.updated_at
        }

    def check_config_data(
        self, app_id: uuid.UUID, data: MultiAgentConfigUpdate
    ) -> MultiAgentConfig:
        # 1. Validate the app exists
        app = self.db.get(App, app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))

        # 2. Validate the model config (when provided)
        if data.default_model_config_id:
            tenant_id = cross_domain_read.get_workspace_tenant_id(self.db, app.workspace_id)
            invoke_ref = resolve_invoke_ref(
                self.db,
                data.default_model_config_id,
                tenant_id=tenant_id,
            )
            if invoke_ref is None:
                raise_model_unavailable(
                    self.db,
                    data.default_model_config_id,
                    tenant_id=tenant_id,
                )

        # 3. Validate sub-agents exist and resolve their release IDs
        # agent_id always stores the sub-agent's app ID (stable identity); the
        # release is expressed by release_policy / release_id: current=follow the
        # latest with release_id cleared; pinned=the stored, validated release_id.
        for sub_agent in (data.sub_agents or []):
            agent_app_release = resolve_release_for_save(
                self.db,
                sub_agent.agent_id,
                sub_agent.release_policy,
                sub_agent.release_id,
                label=sub_agent.name,
            )
            if sub_agent.release_policy == "pinned":
                sub_agent.release_id = agent_app_release.id
            else:
                sub_agent.release_id = None

        # 5. Build the config (convert UUIDs to strings for JSON serialization)
        # sub_agents=None means "keep existing" on the update branch; the create
        # branch fills the NOT NULL column with an empty list.
        sub_agents_data = (
            None
            if data.sub_agents is None
            else [convert_uuids_to_str(sub_agent.model_dump()) for sub_agent in data.sub_agents]
        )
        # routing_rules_data = [
        #     convert_uuids_to_str(rule.model_dump()) for rule in data.routing_rules
        # ] if data.routing_rules else None

        # Handle execution_config (may be None, a dict, or a Pydantic model)
        if data.execution_config is None:
            execution_config_data = {}
        elif isinstance(data.execution_config, dict):
            execution_config_data = convert_uuids_to_str(data.execution_config)
        else:
            execution_config_data = convert_uuids_to_str(data.execution_config.model_dump())

        # Handle supervisor_config (supervisor-as-agent; None=bare supervisor, column stays NULL)
        if getattr(data, "supervisor_config", None) is None:
            supervisor_config_data = None
        elif isinstance(data.supervisor_config, dict):
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config)
        else:
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config.model_dump())

        # Handle model_parameters (may be None, a dict, or a Pydantic model)
        if data.model_parameters is None:
            model_parameters_data = None
        else:
            model_parameters_data = data.model_parameters

        config = MultiAgentConfig(
            app_id=app_id,
            master_agent_id=data.master_agent_id,
            master_agent_name=data.master_agent_name,
            default_model_config_id=data.default_model_config_id,
            model_parameters=model_parameters_data,
            orchestration_mode=data.orchestration_mode,
            sub_agents=sub_agents_data,
            # routing_rules=routing_rules_data,
            execution_config=execution_config_data,
            supervisor_config=supervisor_config_data,
            aggregation_strategy=data.aggregation_strategy
        )
        return config

    def update_config(
        self,
        app_id: uuid.UUID,
        data: MultiAgentConfigUpdate
    ) -> MultiAgentConfig:
        """Update the multi-agent config.

        Args:
            app_id: App ID
            data: Update payload

        Returns:
            The updated config
        """
        config = self.get_config(app_id)
        new_config = self.check_config_data(app_id, data)
        if not config:
            config = new_config
            # "Keep original" has no meaning on the create branch; the NOT NULL
            # sub_agents column defaults to an empty list, mirroring the GET
            # default template.
            if config.sub_agents is None:
                config.sub_agents = []
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            logger.info(
                "创建多 Agent 配置成功",
                extra={
                    "config_id": str(config.id),
                    "app_id": str(app_id),
                    "mode": data.orchestration_mode,
                    "sub_agent_count": len(config.sub_agents)
                }
            )
            return config

        # Fully replace the config; for DB NOT NULL columns, keep the old value
        # when the new value is None
        config.default_model_config_id = new_config.default_model_config_id
        config.model_parameters = new_config.model_parameters
        config.orchestration_mode = new_config.orchestration_mode or config.orchestration_mode
        config.sub_agents = (
            new_config.sub_agents if new_config.sub_agents is not None else config.sub_agents
        )
        config.routing_rules = new_config.routing_rules
        config.execution_config = (
            new_config.execution_config if new_config.execution_config else config.execution_config
        )
        # Supervisor-as-agent: the supervisor_config column is nullable; None=no
        # update / clear to bare supervisor are semantically the same, so assign
        # directly (unlike execution_config's "replace only when non-empty": an
        # explicitly cleared capability surface is the user's intent)
        config.supervisor_config = new_config.supervisor_config
        config.aggregation_strategy = new_config.aggregation_strategy or config.aggregation_strategy
        self.db.commit()
        self.db.refresh(config)

        logger.info(
            "更新多 Agent 配置成功",
            extra={
                "config_id": str(config.id),
                "app_id": str(app_id)
            }
        )

        return config

    async def run(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest
    ) -> dict:
        """Run a multi-agent task (non-streaming).

        Args:
            app_id: App ID
            request: Run request

        Returns:
            Execution result
        """
        # 1. Load the config. This entry point sits on the runtime plane where
        # self.db is an AsyncSession, so the async read is required (the host
        # used the sync get_config here, which cannot run against AsyncSession).
        config = await self.get_config_async(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))

        if not config.is_active:
            # Host parity: the streaming path rejects a disabled config with
            # NOT_FOUND; the host non-streaming path referenced a BizCode member
            # that does not exist and would raise AttributeError, so both port
            # paths use NOT_FOUND.
            raise BusinessException("多 Agent 配置已禁用", BizCode.NOT_FOUND)

        # 2. Build the orchestrator
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        # Pre-generate the assistant message ID and hand it to the orchestrator
        # so the master execution record and the persisted message share one ID;
        # log details attach nodes by message instead of relying on timing order.
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

        # New-conversation opening statement (queried only when
        # features.opening_statement is enabled; legacy clusters have no features,
        # so this costs nothing)
        opening_statement, _opening_suggested_questions = await self._resolve_cluster_opening(
            orchestrator, request.conversation_id, request.variables
        )

        # 3. Execute the task
        result = await orchestrator.execute(
            message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            variables=request.variables,
            use_llm_routing=getattr(request, 'use_llm_routing', True),  # LLM routing, on by default
            web_search=getattr(request, 'web_search', False),  # web search
            memory=getattr(request, 'memory', True),  # memory
            message_id=message_id,
            thinking=getattr(request, 'thinking', False),  # deep thinking (off = legacy behavior)
            files=getattr(request, 'files', None) or None,  # attachments (empty = legacy behavior)
            user_message_id=user_message_id,  # emotion cache key (features.emotion_reply only)
            # opening injected into supervisor history (None = legacy behavior)
            opening_statement=opening_statement,
        )

        # Non-streaming runs persist through the same in-process
        # BatchPersistQueue as the streaming path; direct
        # _save_conversation_message writes are deprecated, and outer callers
        # must not add their own assistant message (duplicate-record root cause).
        from .batch_persist_queue import BatchPersistQueue, PersistTask
        from .chat_context import StreamResult as _StreamResult
        _result = _StreamResult()
        _result.message_id = message_id
        _result.user_message_id = user_message_id
        _result.full_content = result.get("message", "")
        _result.total_tokens = (result.get("usage") or {}).get("total_tokens", 0)
        _result.assistant_meta = {
            "mode": result.get("mode"),
            "elapsed_time": result.get("elapsed_time"),
            "merge_mode_actual": getattr(orchestrator, "_merge_mode_actual", None),
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            })
        }
        # Suggested questions / citations / voice persist like an Agent app;
        # the keys exist only when the matching features are enabled, otherwise
        # no field is written.
        if "suggested_questions" in result:
            _result.suggested_questions = result.get("suggested_questions") or []
        if "citations" in result:
            _result.citations = result.get("citations") or []
        if result.get("audio_url"):
            _result.audio_url = result.get("audio_url")
            _result.audio_status = result.get("audio_status")
        if request.conversation_id is not None:
            _persist_args = {
                "conversation_id": str(request.conversation_id),
                "result": _result,
                "user_message_id_override": user_message_id,
                "user_message_content": request.message,
                # This turn's variable values are persisted on the user row so a
                # cluster regeneration can read them back (key omitted when absent)
                "variables_meta": request.variables or None,
                # The user message attaches to the version the user is currently
                # viewing, so a version switch isolates follow-up turns by parent
                "user_parent_message_id": await self._get_last_current_assistant_id(
                    request.conversation_id
                ),
                "should_memorize": orchestrator.cluster_memory_enabled(),
            }
            # New-conversation opening statement persists with this round (same as
            # Agent apps): the first-turn user attaches under the opening, so the
            # version-switch branch chain can include it
            if opening_statement:
                _persist_args["is_new_conversation"] = True
                _persist_args["opening_statement"] = opening_statement
                _persist_args["opening_message_id"] = uuid.uuid4()
                _persist_args["opening_suggested_questions"] = _opening_suggested_questions
            await BatchPersistQueue.enqueue(PersistTask(
                task_type="save_messages",
                args=_persist_args,
            ))

        # Context-engine after_turn fires only when this turn actually used the
        # context engine; legacy clusters always yield None here.
        _ctx_after_turn = orchestrator.context_engine_after_turn_args(request.conversation_id)
        if _ctx_after_turn:
            try:
                await BatchPersistQueue.enqueue(
                    PersistTask(task_type="after_turn", args=_ctx_after_turn)
                )
            except Exception as e:
                logger.warning(
                    "入队 context_engine after_turn 失败（不影响对话）", extra={"error": str(e)}
                )

        # Backfill the master execution record's message_id after persistence
        # (idempotent; best-effort, never fails the turn).
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(message_id),
                    },
                ))
            except Exception as e:
                logger.warning(
                    "回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)}
                )

        return result

    async def run_stream(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest,
        storage_type: str,
        user_rag_memory_id: str
    ) -> AsyncGenerator[str, None]:
        """Run a multi-agent task (streaming SSE events).

        Args:
            app_id: App ID
            request: Run request
            storage_type: Memory storage type (neo4j / ...)
            user_rag_memory_id: User RAG memory knowledge-base ID

        Yields:
            SSE events
        """
        # 1. Load the config
        config = await self.get_config_async(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))

        if not config.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.NOT_FOUND)

        # 2. Build the orchestrator
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        full_content = ""
        total_tokens = 0
        # Pre-generate the assistant message ID and hand it to the orchestrator:
        # the master execution record gets its message_id by backfill, and log
        # details attach nodes by message instead of "closest in time" (which
        # scatters one turn's nodes across messages under multi-round /
        # concurrent sub-agents).
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

        # New-conversation opening statement (queried only when
        # features.opening_statement is enabled; legacy clusters have no features,
        # so this costs nothing)
        opening_statement, _opening_suggested_questions = await self._resolve_cluster_opening(
            orchestrator, request.conversation_id, request.variables
        )

        # 3. Stream the task. This layer only relays events and accumulates the
        # message body; token accounting is owned by the orchestrator's per-turn
        # ledger (routing/sub/merge) and published once in the end event.
        total_tokens = 0
        # Conversation-capability extras carried by the end event (suggested
        # questions / citations / voice); empty when the features are off.
        end_extras: dict = {}
        async for event in orchestrator.execute_stream(
            message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            variables=request.variables,
            use_llm_routing=getattr(request, 'use_llm_routing', True),
            web_search=getattr(request, 'web_search', False),  # web search
            memory=getattr(request, 'memory', True),  # memory
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            message_id=message_id,
            thinking=getattr(request, 'thinking', False),  # deep thinking (off = legacy behavior)
            files=getattr(request, 'files', None) or None,  # attachments (empty = legacy behavior)
            user_message_id=user_message_id,  # emotion cache key (features.emotion_reply only)
            # opening injected into supervisor history (None = legacy behavior)
            opening_statement=opening_statement,
        ):
            _event_name = ""
            if event.startswith("event:"):
                _event_name = event[6:].split("\n", 1)[0].strip()

            if _event_name == "end" and "data:" in event:
                # The end event carries this turn's usage (ledger view) — the
                # single token source for the persisted meta.
                try:
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    total_tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
                    # Conversation-capability extras (present only when the
                    # matching features are enabled)
                    for _k in ("suggested_questions", "citations", "audio_url", "audio_status"):
                        if _k in data:
                            end_extras[_k] = data[_k]
                except Exception:
                    pass

            yield event
            # Persist only the cluster-level `message` events (decided by event
            # name). Sub-agent bodies arrive as `sub_agent_message`; counting
            # them too would give the persisted assistant body one extra copy
            # versus the UI (bubble grows after refresh).
            if _event_name == "message" and "data:" in event:
                try:
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    if "content" in data:
                        full_content += data["content"]
                except Exception:
                    pass

        # Persistence closure: this service (draft/API scenario) is the single
        # persistence layer for the multi-agent stream — writes go through
        # BatchPersistQueue, and the deprecated direct
        # _save_conversation_message path stays unused. The persisted body = the
        # orchestrator's message event sequence (same view as the frontend main
        # bubble), not an intermediate assembly made by an outer layer.
        import time as _time
        _master_started_at = getattr(orchestrator, "_master_started_at", None)
        elapsed_time = (_time.time() - _master_started_at) if _master_started_at else 0.0
        from .batch_persist_queue import BatchPersistQueue, PersistTask
        from .chat_context import StreamResult as _StreamResult
        _result = _StreamResult()
        _result.message_id = message_id
        _result.user_message_id = user_message_id
        _result.full_content = full_content
        _result.total_tokens = total_tokens
        _result.assistant_meta = {
            "elapsed_time": elapsed_time,
            "mode": getattr(orchestrator, "_normalized_mode", None),
            "merge_mode_actual": getattr(orchestrator, "_merge_mode_actual", None),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
        }
        # Suggested questions / citations / voice persist like an Agent app;
        # empty end_extras writes no field.
        if end_extras:
            if "suggested_questions" in end_extras:
                _result.suggested_questions = end_extras.get("suggested_questions") or []
            if "citations" in end_extras:
                _result.citations = end_extras.get("citations") or []
            if end_extras.get("audio_url"):
                _result.audio_url = end_extras.get("audio_url")
                _result.audio_status = end_extras.get("audio_status")
        await BatchPersistQueue.enqueue(PersistTask(
            task_type="save_messages",
            args={
                "conversation_id": str(request.conversation_id),
                "result": _result,
                "user_message_id_override": user_message_id,
                "user_message_content": request.message,
                # This turn's variable values are persisted on the user row so a
                # cluster regeneration can read them back (key omitted when absent)
                "variables_meta": request.variables or None,
                # The user message attaches to the version the user is currently
                # viewing, so a version switch isolates follow-up turns by parent
                "user_parent_message_id": await self._get_last_current_assistant_id(
                    request.conversation_id
                ),
                "should_memorize": orchestrator.cluster_memory_enabled(),
                # The new-conversation opening statement persists with this round
                # (same as Agent apps) so the version-switch branch chain can
                # include it; key omitted when there is no opening
                **(
                    {
                        "is_new_conversation": True,
                        "opening_statement": opening_statement,
                        "opening_message_id": uuid.uuid4(),
                        "opening_suggested_questions": _opening_suggested_questions,
                    }
                    if opening_statement else {}
                ),
            },
        ))

        # Context-engine after-turn summary (enqueued only when this turn
        # actually ran through the context engine; legacy clusters: None)
        _ctx_after_args = orchestrator.context_engine_after_turn_args(request.conversation_id)
        if _ctx_after_args:
            await BatchPersistQueue.enqueue(
                PersistTask(task_type="after_turn", args=_ctx_after_args)
            )

        # Backfill the master execution record's message_id after persistence
        # (the record is created when the stream starts, before the message row
        # exists — writing message_id there would be an FK violation; idempotent:
        # only records still holding NULL are updated).
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(message_id),
                    },
                ))
            except Exception as e:
                logger.warning(
                    "回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)}
                )

    # ================ Regeneration (multi-version semantics, Agent-aligned) ================

    def _assert_regenerate_quota(self, message_id: uuid.UUID) -> None:
        """Reject when this round already reached the version cap (same rule as WorkflowService).

        Counts undeleted assistant versions under the same parent_message_id
        (including the original reply). Legacy rows where the original message has
        no parent_message_id are not covered by that query, so add 1 for them.
        """
        from ..models import Message
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        parent_msg = self._locate_parent_user_message(original_msg)
        existing = self.db.query(Message).filter(
            Message.parent_message_id == parent_msg.id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
        ).count()
        if not original_msg.parent_message_id:
            existing += 1
        if existing >= self.MAX_REGENERATE_VERSIONS:
            raise BusinessException(
                f"该回复已达最大版本数（{self.MAX_REGENERATE_VERSIONS} 个），无法继续重新生成",
                BizCode.VERSION_LIMIT_EXCEEDED,
            )

    def _locate_parent_user_message(self, original_msg):
        """Locate the parent user message of an original assistant message
        (aligned with WorkflowService._locate_parent_user_message).

        Lookup order:
        1. parent_message_id first; fall back to a time walk when empty/not a user;
        2. the latest user message in the same conversation created at or before the
           original message (not filtered by is_deleted — even deleted, it is still
           the question of this round; <= to tolerate same-millisecond writes);
        3. if still not found, the latest user message in the conversation regardless
           of time (fallback for rows with abnormal created_at).

        When the located parent was soft-deleted (the question was deleted before
        regenerating), restore it and continue instead of failing — an accidentally
        deleted question must not permanently block regeneration of its reply.
        """
        from ..models import Message

        parent_msg = None
        if original_msg.parent_message_id:
            candidate = self.db.get(Message, original_msg.parent_message_id)
            if candidate and candidate.role == "user":
                parent_msg = candidate
        if not parent_msg:
            parent_msg = (
                self.db.query(Message)
                .filter(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at <= original_msg.created_at,
                )
                .order_by(Message.created_at.desc())
                .first()
            )
        if not parent_msg:
            # Fallback: the latest user message in the conversation regardless of
            # time order (covers rows with abnormal created_at)
            parent_msg = (
                self.db.query(Message)
                .filter(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
                .first()
            )
        if not parent_msg:
            raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
        # Backfill parent_message_id for later version linkage; restore a deleted parent
        if original_msg.parent_message_id != parent_msg.id:
            original_msg.parent_message_id = parent_msg.id
        if parent_msg.is_deleted:
            parent_msg.is_deleted = False
        self.db.commit()
        return parent_msg

    def _next_regenerate_version(self, original_msg, parent_msg_id: uuid.UUID) -> int:
        """Compute the new version number: max undeleted assistant version under the
        same parent_message_id + 1.

        With multiple branches the user may switch back to an older version and
        regenerate; using original.version+1 would collide with a newer existing
        version and scramble the version switch, hence max of the group + 1.
        """
        from ..models import Message
        base = original_msg.version or 1
        siblings = self.db.query(Message).filter(
            Message.parent_message_id == parent_msg_id,
            Message.conversation_id == original_msg.conversation_id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
        ).all()
        for s in siblings:
            if s.version and s.version > base:
                base = s.version
        return base + 1

    def _mark_siblings_not_current(self, conversation_id, parent_msg_id, exclude_id=None) -> None:
        """Mark every assistant version of this round (same parent_message_id) non-current.

        Do not rely on conditional ``update().where(is_current.is_(True))`` style
        checks: set False across the whole row set unconditionally; idempotent —
        re-marking False has no side effect.
        """
        from ..models import Message
        q = self.db.query(Message).filter(
            Message.conversation_id == conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        )
        if exclude_id is not None:
            q = q.filter(Message.id != exclude_id)
        q.update({"is_current": False}, synchronize_session=False)
        self.db.commit()

    def _mark_message_current(self, message_id: uuid.UUID, is_current: bool) -> None:
        """Set one message current/non-current by primary key (rollback path; does
        not depend on ORM instance state)."""
        from ..models import Message
        self.db.query(Message).filter(Message.id == message_id).update(
            {"is_current": is_current}, synchronize_session=False
        )
        self.db.commit()

    def _restore_files_from_parent(self, parent_msg):
        """Restore the FileInput list from the parent user meta_data.files (same rule
        as Agent regenerate)."""
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                from .multimodal_service import deserialize_file_reference
                files = []
                for f in meta_files:
                    try:
                        files.append(deserialize_file_reference(f))
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")
                        continue
        return files

    @staticmethod
    def _restore_variables_from_parent(
        parent_msg, override: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Restore the variables supplied on the first turn: the parent user row's
        meta_data.variables as the base, request values overriding on top.

        A regeneration reuses the original question's variables so the user does not
        re-enter them; explicitly passed values win (same merge semantics as workflow
        regeneration). Stored conversations without saved variables return the
        override as-is (possibly None).
        """
        saved = None
        if parent_msg is not None and parent_msg.meta_data:
            saved = parent_msg.meta_data.get("variables")
        if not isinstance(saved, dict) or not saved:
            return override
        merged = dict(saved)
        if override:
            merged.update(override)
        return merged

    async def _resolve_regenerate_config(self, app_id: uuid.UUID, config: MultiAgentConfig | None):
        """Resolve the cluster config for the re-run: when passed in (share-side
        release snapshot) it is used directly; otherwise the draft is loaded.

        Console trial runs use the draft (same source as cluster run); the share
        surface uses the release snapshot. Either missing raises.
        """
        if config is not None:
            return config
        resolved = await self.get_config_async(app_id)
        if not resolved:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        if not resolved.is_active:
            # Carve fix: the host raises with a BizCode member that does not exist
            # (AttributeError at runtime); use NOT_FOUND, same as the run entries.
            raise BusinessException("多 Agent 配置已禁用", BizCode.NOT_FOUND)
        return resolved

    def _build_regenerate_meta(
        self, original_message_id, usage=None, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """meta_data of the new-version assistant message: usage + capability extras
        + regenerated_from provenance."""
        meta: dict[str, Any] = {
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "regenerated_from": str(original_message_id),
        }
        for k in (
            "suggested_questions",
            "citations",
            "audio_url",
            "audio_status",
            "reasoning_content",
        ):
            if extra and extra.get(k):
                meta[k] = extra[k]
        return meta

    def _save_regenerated_message(
        self,
        *,
        conversation_id: uuid.UUID,
        content: str,
        version: int,
        parent_message_id: uuid.UUID,
        meta_data: dict[str, Any],
        message_id: uuid.UUID | None = None,
    ):
        """Persist the new-version assistant message (direct sync insert, same
        pattern as WorkflowService.regenerate).

        The response references the new message_id immediately and the version
        switch must be usable right away, so this bypasses the async
        BatchPersistQueue. Also bumps the conversation's message_count (direct
        inserts are not counted by the queue).
        """
        from ..models import Conversation, Message
        new_msg = Message(
            id=message_id or uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            version=version,
            is_current=True,
            parent_message_id=parent_message_id,
            meta_data=meta_data,
            status="completed",
        )
        self.db.add(new_msg)
        conv = self.db.get(Conversation, conversation_id)
        if conv:
            conv.message_count = int(conv.message_count or 0) + 1
        self.db.commit()
        self.db.refresh(new_msg)
        return new_msg

    async def regenerate(
        self,
        *,
        message_id: uuid.UUID,
        app_id: uuid.UUID,
        user_id: str | None = None,
        variables: dict[str, Any] | None = None,
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        thinking: bool = False,
        config: MultiAgentConfig | None = None,
    ) -> dict[str, Any]:
        """Regenerate a cluster reply (non-streaming, multi-version support).

        Aligned with Agent-app regeneration semantics, but the execution engine is
        the multi-agent orchestrator (supervisor routing / sub-agent orchestration
        preserved). The version-management skeleton matches
        WorkflowService.regenerate: locate parent user message -> mark old versions
        non-current -> take max(version)+1 of the group -> re-run against history
        before the parent (orchestrator regenerate_before truncation) -> persist the
        new version.

        Args:
            message_id: Original assistant message ID
            app_id: Cluster app ID (for config lookup / observability)
            config: Optional. When passed (share-side release snapshot) it is used
                directly; otherwise the current draft is loaded (console trial run).
        """
        from ..models import Message

        # 1. Validate the original message
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. Version quota (rejected up front so an over-limit run fails before orchestration)
        self._assert_regenerate_quota(message_id)

        # 3. Locate the parent user message (auto-restores it when soft-deleted)
        parent_msg = self._locate_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id
        conversation_id = original_msg.conversation_id
        user_message_content = parent_msg.content or ""

        # 4. Version number: max of the group + 1; old versions (the original and its
        # siblings) are all marked non-current
        new_version = self._next_regenerate_version(original_msg, parent_msg_id)
        self._mark_siblings_not_current(conversation_id, parent_msg_id)

        # 5. Restore the parent message's attachments (same rule as Agent regenerate)
        files = self._restore_files_from_parent(parent_msg)
        # Reuse the first turn's variable values (explicit request values override),
        # so the user does not re-enter required variables
        variables = self._restore_variables_from_parent(parent_msg, variables)

        # 6. Resolve the config and rebuild the orchestrator; inject the history
        # truncation time (= parent message created_at)
        resolved_config = await self._resolve_regenerate_config(app_id, config)
        orchestrator = await MultiAgentOrchestrator.create(self.db, resolved_config)

        new_message_id = uuid.uuid4()
        try:
            result = await orchestrator.execute(
                message=user_message_content,
                conversation_id=conversation_id,
                user_id=user_id,
                variables=variables,
                use_llm_routing=True,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                message_id=new_message_id,
                thinking=thinking,
                files=files or None,
                user_message_id=parent_msg_id,  # emotion cache key; no new user row
                opening_statement=None,  # regeneration does not repeat the opening
                regenerate_before=parent_msg.created_at,  # truncate history to before this question
            )
        except Exception:
            # Orchestration failed: restore the original version as current so the
            # conversation never ends up without any current reply
            self._mark_message_current(original_msg.id, True)
            raise

        # 7. Persist the new-version assistant message
        usage = result.get("usage") or {}
        new_msg = self._save_regenerated_message(
            conversation_id=conversation_id,
            content=result.get("message", ""),
            version=new_version,
            parent_message_id=parent_msg_id,
            meta_data=self._build_regenerate_meta(message_id, usage=usage, extra=result),
            message_id=new_message_id,
        )

        # 8. Link the master execution record to the new message (observability
        # attachment; failure never blocks)
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                from .batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(new_message_id),
                    },
                ))
            except Exception as e:
                logger.warning(
                    "回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)}
                )

        logger.info(
            "集群重新生成回复成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_msg.id),
                "version": new_version,
                "conversation_id": str(conversation_id),
            },
        )

        return {
            "message_id": str(new_msg.id),
            "message": result.get("message", ""),
            "reasoning_content": result.get("reasoning_content"),
            "version": new_version,
            "conversation_id": str(conversation_id),
            "suggested_questions": result.get("suggested_questions", []),
            "citations": result.get("citations", []),
            "audio_url": result.get("audio_url"),
            "audio_status": result.get("audio_status"),
        }

    async def regenerate_stream(
        self,
        *,
        message_id: uuid.UUID,
        app_id: uuid.UUID,
        user_id: str | None = None,
        variables: dict[str, Any] | None = None,
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        thinking: bool = False,
        config: MultiAgentConfig | None = None,
    ) -> AsyncGenerator[str, None]:
        """Regenerate a cluster reply (streaming, multi-version support).

        SSE contract aligned with WorkflowService.regenerate_stream:
        start{message_id,user_message_id,version,conversation_id} -> orchestrator
        content events (message/reasoning/sub_agent_* etc.) -> end -> regenerate_end.
        Persistence and version management follow the non-streaming regenerate.
        """
        import asyncio as _asyncio
        import json as _json
        import time as _time

        from ..models import Message

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"

        # 1-4. Validation + quota + parent message + version number + mark old
        # versions non-current (same skeleton as the non-streaming path; must
        # complete before the first SSE event — a clean HTTP error cannot be sent
        # after the stream opens)
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)
        self._assert_regenerate_quota(message_id)
        parent_msg = self._locate_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id
        conversation_id = original_msg.conversation_id
        user_message_content = parent_msg.content or ""
        new_version = self._next_regenerate_version(original_msg, parent_msg_id)
        self._mark_siblings_not_current(conversation_id, parent_msg_id)
        files = self._restore_files_from_parent(parent_msg)
        # Reuse the first turn's variable values (explicit request values override),
        # so the user does not re-enter required variables
        variables = self._restore_variables_from_parent(parent_msg, variables)
        resolved_config = await self._resolve_regenerate_config(app_id, config)
        orchestrator = await MultiAgentOrchestrator.create(self.db, resolved_config)

        new_message_id = uuid.uuid4()
        start_time = _time.time()

        # start event: carries the new message id / version (the frontend mounts
        # this round's bubble and version indicator from it)
        yield _sse("start", {
            "conversation_id": str(conversation_id),
            "message_id": str(new_message_id),
            "user_message_id": str(parent_msg_id),
            "version": new_version,
            "timestamp": start_time,
        })

        full_content = ""
        usage: dict[str, Any] = {}
        end_extras: dict[str, Any] = {}
        stream_failed = False
        error_emitted = False
        failure_error: str | None = None
        try:
            async for event in orchestrator.execute_stream(
                message=user_message_content,
                conversation_id=conversation_id,
                user_id=user_id,
                variables=variables,
                use_llm_routing=True,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                message_id=new_message_id,
                thinking=thinking,
                files=files or None,
                user_message_id=parent_msg_id,
                opening_statement=None,
                regenerate_before=parent_msg.created_at,
            ):
                # Parse the event name to accumulate the body / extract usage and
                # capability extras from the end event
                _ev = ""
                if event.startswith("event:"):
                    _ev = event[6:].split("\n", 1)[0].strip()
                if "data:" in event:
                    try:
                        _data = _json.loads(event.split("data: ", 1)[1].strip())
                    except Exception:
                        _data = {}
                else:
                    _data = {}

                # Passing through the orchestrator's own start/end would produce a
                # duplicate start on the frontend; this method owns the framing and
                # only forwards content/process events.
                if _ev == "start":
                    continue
                if _ev == "end":
                    usage = _data.get("usage") or {}
                    for _k in ("suggested_questions", "citations", "audio_url", "audio_status"):
                        if _k in _data:
                            end_extras[_k] = _data[_k]
                    continue  # the standard end + regenerate_end are emitted at the tail
                if _ev == "error":
                    # execute_stream's failure semantics is an error event (it
                    # swallows the exception and returns normally) — detect it here
                    # and roll back, otherwise empty/partial content would be
                    # persisted as a new version.
                    stream_failed = True
                    failure_error = _data.get("error") or "集群重新生成失败"
                    error_emitted = True  # the orchestrator's error frame was already forwarded
                    yield event
                    break

                if _ev == "message" and "content" in _data:
                    full_content += _data["content"]
                yield event
        except (GeneratorExit, _asyncio.CancelledError):
            # Consumer disconnected mid-stream (GeneratorExit / task cancellation are
            # BaseException subclasses, not caught by the except Exception below):
            # the old version has already been marked non-current and the new version
            # is not persisted yet, so skipping the rollback would leave every version
            # of this round with is_current=false. No yield is allowed here — finish
            # the DB bookkeeping and re-raise.
            try:
                self._mark_message_current(original_msg.id, True)
            except Exception as rollback_err:
                logger.warning("断开后回滚原版本失败", extra={"error": str(rollback_err)})
            raise
        except Exception as e:
            stream_failed = True
            failure_error = str(e)

        # Failure rollback: restore the original version as current, persist no new
        # version. The failure frame is an error event (same semantics as Agent /
        # orchestrator failures), not an end with status=failed; when the
        # orchestrator already forwarded an error frame, do not emit another.
        if stream_failed:
            self._mark_message_current(original_msg.id, True)
            if not error_emitted:
                yield _sse("error", {
                    "conversation_id": str(conversation_id),
                    "error": failure_error or "集群重新生成失败",
                    "elapsed_time": _time.time() - start_time,
                    "timestamp": _time.time(),
                })
            return

        # 7. Persist the new-version assistant message
        new_msg = self._save_regenerated_message(
            conversation_id=conversation_id,
            content=full_content,
            version=new_version,
            parent_message_id=parent_msg_id,
            meta_data=self._build_regenerate_meta(message_id, usage=usage, extra=end_extras),
            message_id=new_message_id,
        )

        # 8. Link the master execution record (observability attachment; failure
        # never blocks)
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                from .batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(new_message_id),
                    },
                ))
            except Exception as e:
                logger.warning(
                    "回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)}
                )

        logger.info(
            "集群重新生成回复成功（流式）",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_msg.id),
                "version": new_version,
                "conversation_id": str(conversation_id),
            },
        )

        # 9. Standard end + regenerate_end (same contract as workflow regeneration)
        yield _sse("end", {
            "status": "completed",
            "elapsed_time": _time.time() - start_time,
            "conversation_id": str(conversation_id),
            "message_id": str(new_message_id),
            "message_length": len(full_content),
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            **end_extras,
        })
        yield _sse("regenerate_end", {
            "message_id": str(new_message_id),
            "version": new_version,
            "conversation_id": str(conversation_id),
        })
