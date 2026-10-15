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

    def __init__(self, db: Session):
        self.db = db

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
            await BatchPersistQueue.enqueue(PersistTask(
                task_type="save_messages",
                args={
                    "conversation_id": str(request.conversation_id),
                    "result": _result,
                    "user_message_id_override": user_message_id,
                    "user_message_content": request.message,
                    "should_memorize": orchestrator.cluster_memory_enabled(),
                },
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
                "should_memorize": orchestrator.cluster_memory_enabled(),
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
