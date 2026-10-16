"""App chat kernel: Agent / multi-agent cluster / workflow chat entry points.

Frozen port of the monolith ``AppChatService`` backing the public-share chat
face (and, later, the v1 chat face). The class supports both session kinds:
async sessions drive the chat-kernel paths (share streaming), sync sessions
back management-side replays.

Delegation follows the host split: file validation, variable preparation,
opening statements, suggested questions, citations, TTS and execution-record
writes live on ``AgentRunService``; conversation writes and memory-pair
dispatch live on ``ConversationService``; model credentials and the runtime
model view come from the model service bridges.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any, AsyncGenerator, Dict, List, Literal, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..agent.langchain_agent import LangChainAgent
from ..config import current_settings
from ..db import get_async_db_context, get_db
from ..errors import BizCode, BusinessException
from ..integrations.knowledge.contracts import KnowledgeRetrievalSource
from ..memory.emotion.emotion_resolver import (
    apply_detection as apply_emotion_detection,
)
from ..memory.emotion.emotion_resolver import (
    start_detection as start_emotion_detection,
)
from ..memory.retrieval_trace.message_trace import (
    MessageTrace,
    merge_memory_trace,
    strip_memory_trace_transients,
)
from ..memory.retrieval_trace.stage_events import (
    capture_memory_stage_stream,
    memory_stage_capture,
)
from ..model_clients.embedding import RedBearEmbeddings
from ..models import (
    AgentConfig,
    AgentExecution,
    App,
    Conversation,
    Message,
    Modality,
    ModelFeature,
    MultiAgentConfig,
    WorkflowConfig,
)
from ..models.file_metadata_model import FileMetadata
from ..repositories.agent_execution_repository import AgentExecutionRepository
from ..repositories.tool_repository import ToolRepository
from ..schemas import DraftRunRequest
from ..schemas.app_schema import Citation, FileInput, FileType
from ..schemas.prompt_schema import PromptMessageRole, render_prompt_message
from ..usage_context import bind_usage
from ..utils.datetime_utils import parse_timestamp_to_utc_naive
from .annotation_service import AnnotationService
from .context_assembler import (
    ContextEvidence,
    append_external_context_rule,
)
from .context_engine_manager import ContextEngineManager
from .conversation_service import ConversationService
from .draft_run_service import AgentRunService, build_uploaded_images_manifest
from .model_service import ModelApiKeyService, ModelConfigService
from .multi_agent_orchestrator import MultiAgentOrchestrator
from .multimodal_service import (
    MultimodalService,
    deserialize_file_reference,
    sanitize_processed_files_for_history,
    serialize_file_reference,
)
from .tool_orchestrator import ToolOrchestrator
from .workflow_service import WorkflowService

logger = logging.getLogger(__name__)


def _snapshot_annotations(annotations: List[Any]) -> List[SimpleNamespace]:
    """Copy ORM values while the async database session is still open."""
    return [SimpleNamespace(
        id=item.id,
        question=item.question,
        answer=item.answer,
        embedding=list(item.embedding) if item.embedding else None,
        created_at=item.created_at,
        updated_at=item.updated_at,
    ) for item in annotations]


class CustomJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def assert_not_opening_statement(db, message_id: uuid.UUID) -> None:
    """Reject regenerating the opening statement (first assistant message, no
    parent_message_id and no preceding user message) — regeneration replays
    context from the parent user message.

    Must run before the StreamingResponse: once SSE has started (200 OK), an
    exception raised inside the generator cannot be turned into a clean HTTP
    error by FastAPI, so the front end would show a blank page instead of a
    warning toast. Raising BusinessException here turns into 400 beforehand.

    A soft-deleted user message still counts as a preceding question —
    ``_locate_or_restore_parent_user_message`` restores it and regeneration
    continues. Filtering deleted rows out here would misclassify "user deleted
    the question and wants to regenerate" as an opening statement.
    """
    from sqlalchemy import select
    msg = db.get(Message, message_id)
    # Only assistant messages are relevant; non-assistant / missing rows are
    # left to the validation inside regenerate.
    if not msg or msg.role != "assistant":
        return
    # A parent user message means it is not an opening statement.
    if msg.parent_message_id:
        return
    # No parent: reuse the regenerate_stream fallback — look for a preceding
    # user message; none means opening statement.
    preceding_user = db.scalars(
        select(Message).where(
            Message.conversation_id == msg.conversation_id,
            Message.role == "user",
            Message.created_at <= msg.created_at,
        ).order_by(Message.created_at.desc()).limit(1)
    ).first()
    if not preceding_user:
        raise BusinessException("该消息是开场白，不支持重新生成", BizCode.BAD_REQUEST)


class AppChatService:
    """App chat kernel (agent / cluster / workflow modes)."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db
        self.conversation_service: ConversationService = ConversationService(db)
        self.agent_service: AgentRunService = AgentRunService(db)
        self.workflow_service: WorkflowService = WorkflowService(db)

    def _uses_async_session(self) -> bool:
        return isinstance(self.db, AsyncSession)

    def _resolve_tenant_id(self, workspace_id: Optional[str]) -> Optional[uuid.UUID]:
        if not workspace_id:
            return None
        return ToolRepository.get_tenant_id_by_workspace_id(self.db, str(workspace_id))

    async def _resolve_tenant_id_async(self, workspace_id: Optional[str]) -> Optional[uuid.UUID]:
        if not workspace_id:
            return None
        if self._uses_async_session():
            return await ToolRepository.get_tenant_id_by_workspace_id_async(self.db, str(workspace_id))
        return self._resolve_tenant_id(workspace_id)

    async def _resolve_app_tenant_id_async(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        if not self._uses_async_session():
            return self._resolve_app_tenant_id(app_id)

        async with get_async_db_context() as db:
            app = await db.get(App, app_id)
            if not app:
                return None
            return await ToolRepository.get_tenant_id_by_workspace_id_async(db, str(app.workspace_id))

    def _resolve_app_tenant_id(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        app = self.db.get(App, app_id)
        if not app:
            return None
        return self._resolve_tenant_id(str(app.workspace_id))

    async def _db_get(self, model, identity):
        if self._uses_async_session():
            return await self.db.get(model, identity)
        return self.db.get(model, identity)

    async def _get_last_current_assistant_id(self, conversation_id) -> Optional[uuid.UUID]:
        """Last assistant message marked is_current before this turn's question.

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

    async def _fetch_completed_file_metadata(self, local_ids: list[uuid.UUID]) -> dict[str, SimpleNamespace]:
        if not local_ids:
            return {}
        async with get_async_db_context() as db:
            result = await db.execute(
                select(
                    FileMetadata.id,
                    FileMetadata.file_name,
                    FileMetadata.file_size,
                ).where(
                    FileMetadata.id.in_(local_ids),
                    FileMetadata.status == "completed",
                )
            )
            # Copy plain values before the session closes so callers never
            # touch detached ORM instances.
            return {
                str(row.id): SimpleNamespace(
                    file_name=row.file_name,
                    file_size=row.file_size,
                )
                for row in result.all()
            }

    async def _conversation_has_messages(self, conversation_id: uuid.UUID) -> bool:
        if self._uses_async_session():
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
        existing_messages = self.conversation_service.message_repo.get_message_by_conversation_id(
            conversation_id=conversation_id,
            limit=1,
        )
        return len(existing_messages) > 0

    async def _resolve_cluster_opening(
            self,
            orchestrator: Any,
            conversation_id: uuid.UUID,
            variables: Optional[Dict[str, Any]],
    ) -> tuple:
        """Cluster opening statement: only query the new-conversation state when
        features.opening_statement is enabled.

        Backward compatible: existing clusters without features return
        (None, []) without any extra query. Failures degrade to no opening
        statement and never block the turn.
        """
        features = getattr(orchestrator, "_features", None) or {}
        opening_cfg = features.get("opening_statement")
        if not (isinstance(opening_cfg, dict) and opening_cfg.get("enabled") and opening_cfg.get("statement")):
            return None, []
        try:
            is_new_conversation = not await self._conversation_has_messages(conversation_id)
            from .cluster_chat_features import resolve_opening

            return resolve_opening(features, variables, is_new_conversation)
        except Exception as e:  # noqa: BLE001 - opening statement is an additive capability
            logger.warning(f"集群开场白解析失败（已跳过）: {e}")
            return None, []

    async def _cluster_annotation_hit(
            self,
            config: Any,
            message: str,
            conversation_id: uuid.UUID,
            message_id: uuid.UUID,
            user_message_id: uuid.UUID,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            source: str = "",
    ) -> Optional[dict]:
        """Cluster annotation hit (aligned with Agent apps): on a hit persist
        the user/assistant messages and return the annotation result.

        Backward compatible: the app's AppAnnotationSetting is looked up by
        app_id — not configured / disabled / no annotations all return None and
        the caller proceeds with normal orchestration; any exception also
        degrades to None (annotations are additive and never block the turn).
        Memory dispatch only runs when the cluster memory switch
        (supervisor_config.memory.enabled) is on.
        """
        try:
            from ..models.annotation_model import HitLogSource

            match = await self._check_annotation_match_async(
                config.app_id, message, source=source or HitLogSource.EXTERNAL
            )
            if not match:
                return None
            answer = match["answer"]
            # The user message attaches to the version the user is currently
            # viewing (must be queried before this turn's messages are written)
            _user_parent_id = await self._get_last_current_assistant_id(conversation_id)
            await self.conversation_service.add_message_async(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data={"files": []},
                parent_message_id=_user_parent_id,
            )
            await self.conversation_service.add_message_async(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                meta_data={"usage": {}},
            )
            supervisor_config = getattr(config, "supervisor_config", None)
            if hasattr(supervisor_config, "model_dump"):
                supervisor_config = supervisor_config.model_dump()
            memory_cfg = (supervisor_config or {}).get("memory") if isinstance(supervisor_config, dict) else None
            if isinstance(memory_cfg, dict) and memory_cfg.get("enabled"):
                await self.conversation_service.dispatch_memory_pair(
                    conversation_id,
                    user_message={"id": user_message_id, "content": message, "meta_data": {"files": []}},
                    assistant_message={"id": message_id, "content": answer, "meta_data": {"usage": {}}},
                    storage_type=storage_type or "neo4j",
                    user_rag_memory_id=user_rag_memory_id or "",
                )
            return match
        except Exception as e:  # noqa: BLE001 - annotations are an additive capability
            logger.warning(f"集群标注命中处理失败（已跳过，走正常编排）: {e}")
            return None

    async def _build_cluster_files_persist(
            self,
            files: list[FileInput],
            orchestrator: Any,
    ) -> tuple:
        """Persisted file payload of one cluster turn (same shape as Agent apps).

        Returns:
            (files_meta, history_files): files_meta goes into the user message
            meta.files; history_files is non-None only when the supervisor
            actually processed attachments (used to hydrate later history).
        """
        files_meta: list = []
        local_ids = [
            f.upload_file_id for f in files
            if f.transfer_method.value == "local_file" and f.upload_file_id
            and (not f.name or not f.size)
        ]
        meta_map = await self._fetch_completed_file_metadata(local_ids) if local_ids else {}
        for f in files:
            name, size = f.name, f.size
            if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                meta = meta_map.get(str(f.upload_file_id))
                if meta:
                    name = name or meta.file_name
                    size = size or meta.file_size
            files_meta.append(serialize_file_reference(f, name=name, size=size))

        history_files = None
        processed_files = getattr(orchestrator, "_turn_processed_files", None)
        if processed_files:
            history_files = {
                "content": sanitize_processed_files_for_history(processed_files),
                "provider": getattr(orchestrator, "_turn_files_provider", None),
            }
        return files_meta, history_files

    async def _record_api_key_usage(self, api_key_id: uuid.UUID | None) -> bool:
        if not api_key_id:
            return False
        if self._uses_async_session():
            async with get_async_db_context() as db:
                return await ModelApiKeyService.record_api_key_usage_bridge_async(db, api_key_id)
        return await ModelApiKeyService.record_api_key_usage_bridge_async(self.db, api_key_id)

    async def _release_db_connection(self) -> None:
        try:
            if self._uses_async_session():
                await self.db.close()
            else:
                self.db.close()
        except Exception as e:
            logger.warning(f"Failed to release DB connection: {e}")

    @staticmethod
    def _append_history_message(
            history: Optional[List[dict]],
            *,
            role: str,
            content: Any,
    ) -> List[dict]:
        next_history = list(history or [])
        next_history.append({"role": role, "content": content})
        return next_history

    async def _create_agent_execution(self, repo: AgentExecutionRepository, execution: AgentExecution) -> uuid.UUID:
        if self._uses_async_session():
            async with get_async_db_context() as db:
                db.add(execution)
                await db.commit()
                await db.refresh(execution)
                return execution.id
        created = repo.create(execution)
        self.db.commit()
        return created.id

    async def _update_agent_execution(
            self,
            repo: AgentExecutionRepository,
            execution_id: uuid.UUID,
            **kwargs,
    ) -> None:
        if self._uses_async_session():
            async with get_async_db_context() as db:
                await AgentExecutionRepository(db).update_completed_async(execution_id, **kwargs)
            return
        repo.update_completed(execution_id, **kwargs)

    async def _check_annotation_match_async(
            self,
            app_id: uuid.UUID,
            message: str,
            source: str = "",
    ) -> Optional[dict]:
        if not self._uses_async_session():
            return self._check_annotation_match(app_id, message, source)

        try:
            from ..models.annotation_model import (
                AppAnnotation,
                AppAnnotationHitLog,
                AppAnnotationSetting,
            )

            async with get_async_db_context() as db:
                result = await db.execute(
                    select(AppAnnotationSetting).where(AppAnnotationSetting.app_id == app_id).limit(1)
                )
                setting = result.scalar_one_or_none()
                if not setting or not setting.enabled or not setting.model_config_id:
                    return None

                result = await db.execute(
                    select(AppAnnotation).where(
                        AppAnnotation.app_id == app_id,
                        AppAnnotation.is_active == 1,
                    )
                )
                annotations = _snapshot_annotations(list(result.scalars().all()))
                if not annotations:
                    return None

                tenant_id = await self._resolve_app_tenant_id_async(app_id)
                embedding_ref = await ModelApiKeyService.resolve_invoke_ref_async(
                    db,
                    setting.model_config_id,
                    tenant_id=tenant_id,
                )
                if not embedding_ref:
                    return None
                threshold = setting.similarity_threshold

            query_embedding = await RedBearEmbeddings.for_invoke(embedding_ref).aembed_query(message)
            best_match = None
            best_similarity = 0.0
            for annotation in annotations:
                if not annotation.embedding:
                    continue
                similarity = AnnotationService.cosine_similarity(query_embedding, annotation.embedding)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = annotation

            if not best_match or best_similarity < threshold:
                return None

            async with get_async_db_context() as db:
                annotation = await db.get(AppAnnotation, best_match.id)
                if not annotation:
                    return None
                annotation.hit_count = int(annotation.hit_count or 0) + 1
                db.add(
                    AppAnnotationHitLog(
                        annotation_id=annotation.id,
                        app_id=app_id or annotation.app_id,
                        source=source,
                        query=message,
                        matched_question=annotation.question,
                        answer=annotation.answer,
                        similarity=best_similarity,
                    )
                )
                await db.commit()

            return {
                "annotation_id": str(best_match.id),
                "question": best_match.question,
                "answer": best_match.answer,
                "similarity": best_similarity,
            }
        except Exception as e:
            logger.error(f"标注匹配失败: {e}")
            return None

    def _check_annotation_match(self, app_id: uuid.UUID, message: str, source: str = "") -> Optional[dict]:
        """Check whether the message hits an annotation.

        Args:
            app_id: Application ID.
            message: User message.
            source: Source (recorded as the hit origin).

        Returns:
            The annotation result dict on a hit, None otherwise.
        """
        try:
            service = AnnotationService(self.db)
            setting = service.get_setting(app_id)
            if not setting or not setting.enabled:
                return None
            if not setting.model_config_id:
                return None

            annotations = service.repo.get_all_active_by_app(app_id)
            if not annotations:
                return None

            tenant_id = self._resolve_app_tenant_id(app_id)
            embedding_ref = ModelApiKeyService.resolve_invoke_ref(
                self.db,
                setting.model_config_id,
                tenant_id=tenant_id,
            )
            if not embedding_ref:
                return None

            result = service.find_best_match(
                query=message,
                annotations=annotations,
                threshold=setting.similarity_threshold,
                embedding_ref=embedding_ref,
                app_id=app_id,
                source=source,
            )
            return result
        except Exception as e:
            logger.error(f"标注匹配失败: {e}")
            return None

    async def _load_annotation_context_evidence(self, app_id: uuid.UUID, message: str) -> List[ContextEvidence]:
        """Lazily query candidate annotations; only triggered by knowledge-base
        or memory tools that produced valid evidence."""
        try:
            from ..models.annotation_model import AppAnnotation, AppAnnotationSetting
            embedding_ref = None
            if self._uses_async_session():
                async with get_async_db_context() as db:
                    setting = (await db.execute(select(AppAnnotationSetting).where(
                        AppAnnotationSetting.app_id == app_id).limit(1))).scalar_one_or_none()
                    if not setting or not setting.enabled or not setting.model_config_id:
                        return []
                    annotations = _snapshot_annotations(list((await db.execute(select(AppAnnotation).where(
                        AppAnnotation.app_id == app_id, AppAnnotation.is_active == 1))).scalars().all()))
                    tenant_id = await self._resolve_app_tenant_id_async(app_id)
                    embedding_ref = await ModelApiKeyService.resolve_invoke_ref_async(
                        db, setting.model_config_id, tenant_id=tenant_id)
            else:
                service = AnnotationService(self.db)
                setting = service.get_setting(app_id)
                if not setting or not setting.enabled or not setting.model_config_id:
                    return []
                annotations = service.repo.get_all_active_by_app(app_id)
                embedding_ref = ModelApiKeyService.resolve_invoke_ref(
                    self.db, setting.model_config_id, tenant_id=self._resolve_app_tenant_id(app_id))
            if not annotations or not embedding_ref:
                return []
            candidates = await asyncio.to_thread(
                AnnotationService.find_context_candidates,
                message, annotations, embedding_ref, 0.6, 3,
            )
            logger.info(
                "[上下文组装] 标注候选 | "
                f"应用={str(app_id)[:8]} | 标注总数={len(annotations)} | "
                f"候选={len(candidates)} | 阈值=0.6 | top_k=3",
                extra={
                    "app_id": str(app_id),
                    "annotation_total": len(annotations),
                    "candidate_count": len(candidates),
                    "threshold": 0.6,
                    "top_k": 3,
                },
            )
            return [ContextEvidence(
                source_type="annotation", source_id=item["annotation_id"],
                content=f"参考问题：{item['question']}\n参考答案：{item['answer']}",
                score=item["similarity"], created_at=item.get("created_at"),
                updated_at=item.get("updated_at"), metadata={"question": item["question"]},
            ) for item in candidates]
        except Exception:
            logger.warning("标注上下文候选加载失败", exc_info=True)
            return []

    @bind_usage("app", "config.app_id")
    async def agent_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            files: list[FileInput],
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            workspace_id: Optional[str] = None,
            source: str = "",
            history: Optional[List[Dict[str, Any]]] = None,
            skip_save: bool = False,
            parent_message_id: Optional[uuid.UUID] = None,
            version: int = 1,
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
    ) -> Dict[str, Any]:
        """Agent chat (non-streaming)."""
        start_time = time.time()
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

        # Annotation hit check
        from ..models.annotation_model import HitLogSource
        annotation_match = await self._check_annotation_match_async(
            config.app_id,
            message,
            source=source or HitLogSource.EXTERNAL
        )
        if annotation_match:
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()
            await self.conversation_service.add_message_async(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data={"files": []}
            )
            await self.conversation_service.add_message_async(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=annotation_match["answer"],
                meta_data={"usage": {}}
            )
            await self.conversation_service.dispatch_memory_pair(
                conversation_id,
                user_message={"id": user_message_id, "content": message, "meta_data": {"files": []}},
                assistant_message={"id": message_id, "content": annotation_match["answer"], "meta_data": {"usage": {}}},
                storage_type=storage_type or "neo4j",
                user_rag_memory_id=user_rag_memory_id or "",
            )
            elapsed_time = time.time() - start_time
            return {
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "user_message_id": str(user_message_id),
                "message": annotation_match["answer"],
                "reasoning_content": None,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "elapsed_time": elapsed_time,
                "suggested_questions": [],
                "citations": [],
                "audio_url": None,
                "audio_status": None
            }

        # Application features config
        features_config: dict = config.features or {}
        if hasattr(features_config, 'model_dump'):
            features_config = features_config.model_dump()
        web_search_feature = features_config.get("web_search", {})
        if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
            web_search = False

        # Validate file upload
        self.agent_service._validate_file_upload(features_config, files)

        # Emotion-aware reply (app switch; disabled returns None and skips all
        # emotion handling). Started early so it runs concurrently with tool /
        # prompt / context-engine / multimodal preparation instead of adding
        # serial first-token latency; the result is awaited and injected right
        # before ToolOrchestrator / LangChainAgent creation.
        emotion_detection = start_emotion_detection(features_config, message)

        variables = self.agent_service.prepare_variables(variables, config.variables)

        # Resolve model config id
        model_config_id = config.default_model_config_id
        tenant_id = await self._resolve_tenant_id_async(workspace_id)
        api_key_obj = await ModelApiKeyService.resolve_runtime_api_key_bridge_or_raise_async(
            self.db,
            model_config_id,
            tenant_id=tenant_id,
        )
        if not api_key_obj:
            await ModelConfigService.raise_model_unavailable_bridge_async(
                self.db, model_config_id, tenant_id=tenant_id
            )
        # Remote mode: the non-decrypted view feeds the invoke seam and
        # multimodal formatting; credentials on the key object still serve
        # sandbox / usage consumers.
        model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
            self.db,
            model_config_id,
            tenant_id=tenant_id,
        )
        # Render the system prompt (variable substitution supported)
        system_prompt = config.system_prompt
        if variables:
            system_prompt_rendered = render_prompt_message(
                system_prompt,
                PromptMessageRole.USER,
                variables
            )
            system_prompt = system_prompt_rendered.get_text_content() or system_prompt

        # Load tools / skills / knowledge / memory configs in parallel (each
        # opens its own DB session; no cross dependencies)
        tools = []
        _ws_id = uuid.UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        coros = [
            self.agent_service.load_tools_config(config.tools, web_search, tenant_id, user_id, workspace_id),
            self.agent_service.load_skill_config(config.skills, message, tenant_id, user_id, workspace_id),
            self.agent_service.load_knowledge_retrieval_config(
                config.knowledge_retrieval,
                user_id,
                app_id=config.app_id,
                workspace_id=_ws_id,
                source=KnowledgeRetrievalSource.AGENT,
            ),
        ]
        if memory:
            coros.append(
                self.agent_service.load_memory_config(config.memory, user_id, _ws_id, storage_type, user_rag_memory_id)
            )
        parallel_results = await asyncio.gather(*coros, return_exceptions=True)

        base_tools = parallel_results[0]
        skill_result = parallel_results[1]
        kb_result = parallel_results[2]
        memory_result = parallel_results[3] if memory else None

        if not isinstance(base_tools, Exception):
            tools.extend(base_tools)
        else:
            logger.warning("load_tools_config failed: %s", base_tools)

        if not isinstance(skill_result, Exception) and skill_result is not None:
            skill_tools, skill_prompts = skill_result
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
        elif isinstance(skill_result, Exception):
            logger.warning("load_skill_config failed: %s", skill_result)

        if not isinstance(kb_result, Exception) and kb_result is not None:
            kb_tools, citations_collector = kb_result
            tools.extend(kb_tools)
        elif isinstance(kb_result, Exception):
            logger.warning("load_knowledge_retrieval_config failed: %s", kb_result)
            citations_collector = []

        if memory_result is not None and not isinstance(memory_result, Exception):
            memory_tools, _ = memory_result
            tools.extend(memory_tools)
        elif isinstance(memory_result, Exception):
            logger.warning("load_memory_config failed: %s", memory_result)

        # Model parameters
        model_parameters = config.model_parameters
        async def load_annotation_context():
            return await self._load_annotation_context_evidence(config.app_id, message)

        system_prompt = append_external_context_rule(system_prompt)

        # Load history (including the opening statement)
        used_context_engine = False
        if history is None:
            context_engine_manager = ContextEngineManager(self.db)
            prepared_input = await context_engine_manager.prepare_app_agent_input(
                features=features_config,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                current_input=message,
                current_provider=api_key_obj.provider,
                legacy_max_history=current_settings().agent_max_history,
                model_config_id=config.default_model_config_id,
            )
            if prepared_input:
                system_prompt, history = prepared_input
                used_context_engine = True
            else:
                history = await self.conversation_service.get_conversation_history(
                    conversation_id=conversation_id,
                    max_history=current_settings().agent_max_history,
                    current_provider=api_key_obj.provider,
                )

        # Opening statement on a new conversation goes into the database as
        # the first assistant message
        is_new_conversation = not await self._conversation_has_messages(conversation_id)
        if is_new_conversation:
            opening, suggested_questions = self.agent_service._get_opening_statement(features_config, True, variables)
            if opening:
                await self.conversation_service.add_message_async(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=opening,
                    meta_data={"suggested_questions": suggested_questions}
                )
                # Reload history (now including the freshly written opening)
                history = await self.conversation_service.get_conversation_history(
                    conversation_id=conversation_id,
                    max_history=current_settings().agent_max_history,
                    current_provider=api_key_obj.provider,
                )

        # Process multimodal files
        processed_files = None
        # LLM-facing user message only (appends this turn's image manifest);
        # never pollutes the stored original ``message``.
        llm_message = message
        if files:
            multimodal_service = MultimodalService(self.db, model_view)
            fu_config = features_config.get("file_upload", {})
            if hasattr(fu_config, "model_dump"):
                fu_config = fu_config.model_dump()
            doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
            processed_files = await multimodal_service.process_files(
                files,
                document_image_recognition=doc_img_recognition,
                workspace_id=workspace_id,
                file_upload_config=fu_config if isinstance(fu_config, dict) else None,
            )
            logger.info(f"处理了 {len(processed_files)} 个文件")
            for tool in tools:
                set_uploaded_files = getattr(tool, "set_uploaded_files", None)
                if callable(set_uploaded_files):
                    set_uploaded_files(files)
            # List this turn's images (with indexes) in the LLM-facing message
            # so the model can explicitly trigger image retrieval on demand.
            image_manifest, _ = build_uploaded_images_manifest(files)
            if image_manifest:
                llm_message = f"{message}\n\n{image_manifest}"
            if doc_img_recognition and Modality.IMAGE in (api_key_obj.input_modalities or []) and any(
                f.type == FileType.DOCUMENT for f in files
            ):
                system_prompt += (
                    "\n\n文档文字中可能包含图片位置标记如 [图片 第2页 第1张]。"
                    "对应图片已作为独立视觉输入提供，请结合位置标记和视觉内容理解文档；"
                    "不要在回答中输出任何文件地址、存储路径或内部标识。"
                )

        # Emotion-aware reply: await the detection result → on success write
        # the cache and inject "fixed principles + current-turn strategy";
        # failure / no hit leaves the prompt untouched. Must complete before
        # ToolOrchestrator / LangChainAgent so they see the final prompt.
        # write_cache=memory: with memory off the dispatcher fires no fast
        # write, the cache has no consumer, and skipping avoids orphan keys.
        system_prompt = await apply_emotion_detection(
            system_prompt, emotion_detection, user_message_id, write_cache=memory
        )

        # Weak models: drive multi-round tool calls with the ReAct prompt and
        # inject the trace into system_prompt
        features = [str(item) for item in (api_key_obj.features or [])]
        orchestrator_node_executions = []
        _api_key_config = {
            "model_name": api_key_obj.model_name,
            "api_key": api_key_obj.api_key,
            "provider": api_key_obj.provider,
            "api_base": api_key_obj.api_base,
            "input_modalities": [str(item) for item in (api_key_obj.input_modalities or [])],
            "output_modalities": [str(item) for item in (api_key_obj.output_modalities or [])],
            "features": features,
            # Legacy fields frozen at the sandbox payload shape (until the
            # e2b infra retires in the same batch)
            "is_omni": api_key_obj.is_omni,
            "capability": [str(item) for item in (api_key_obj.capability or [])],
            "tenant_id": api_key_obj.tenant_id,
            "model_config_id": api_key_obj.model_config_id,
            "channel_id": api_key_obj.channel_id,
        }
        use_agent_mode = ModelFeature.FUNCTION_CALL in features
        if not use_agent_mode and tools:
            system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                tools=tools,
                system_prompt=system_prompt,
                message=message,
                history=history,
                model_view=model_view,
                effective_params=model_parameters,
                processed_files=processed_files,
                context_evidence_loader=load_annotation_context,
            )
            tools = []

        # Branch: sandbox vs in-process execution
        _sandbox_adapter = None
        if execution_mode == "sandbox":
            from .e2b_agent_adapter import E2BAgentAdapter

            _ws_id = uuid.UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
            sandbox_payload = await self.agent_service._build_sandbox_payload(
                agent_config=config,
                model_config=None,
                api_key_config=_api_key_config,
                effective_params=model_parameters,
                message=message,
                system_prompt=system_prompt,
                workspace_id=_ws_id,
                user_id=user_id,
                conversation_id=str(conversation_id),
                tools=tools,
                history=history,
                context=None,
                variables=variables,
                files_config=features_config.get("file_upload", {}),
            )
            _sandbox_adapter = E2BAgentAdapter(self.db)

            # Create agent execution record
            agent_exec_repo = AgentExecutionRepository(self.db)
            app_obj = await self._db_get(App, config.app_id)
            agent_execution = AgentExecution(
                app_id=config.app_id,
                conversation_id=conversation_id,
                message_id=None,
                agent_config_id=config.id,
                release_id=app_obj.current_release_id if app_obj else None,
                triggered_by=None,
                steps=[],
                status="running",
                started_at=parse_timestamp_to_utc_naive(start_time),
                meta_data={
                    "model": api_key_obj.model_name,
                    "provider": api_key_obj.provider,
                },
            )
            agent_execution_id = await self._create_agent_execution(agent_exec_repo, agent_execution)

            try:
                raw_result = await _sandbox_adapter.run(sandbox_payload=sandbox_payload)
                result = {
                    "content": raw_result.get("content", ""),
                    "reasoning_content": "",
                    "usage": raw_result.get("usage", {}),
                    "node_executions": raw_result.get("node_executions", []),
                }
            except Exception as e:
                elapsed_time = time.time() - start_time
                await self._update_agent_execution(
                    agent_exec_repo,
                    execution_id=agent_execution_id,
                    steps=[],
                    status="failed",
                    elapsed_time=elapsed_time,
                    error_message=str(e)[:2000],
                )
                raise
        else:
            # Create the LangChain Agent (remote mode: identity/capability
            # facts from the non-decrypted view, credentials stay server side)
            agent = LangChainAgent(
                model_name=api_key_obj.model_name,
                model_view=model_view,
                provider=api_key_obj.provider,
                temperature=model_parameters.get("temperature", 0.7),
                max_tokens=model_parameters.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                deep_thinking=model_parameters.get("deep_thinking", False),
                thinking_budget_tokens=model_parameters.get("thinking_budget_tokens"),
                json_output=model_parameters.get("json_output", False),
                context_query=message,
                context_base_text=system_prompt + "\n" + str(history) + "\n" + message,
                context_evidence_loader=load_annotation_context,
            )

            # Inject runtime context into tools that need it
            for t in tools:
                if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                    t.tool_instance.set_runtime_context(
                        user_id=user_id or "anonymous",
                        conversation_id=str(conversation_id) if conversation_id else None,
                        uploaded_files=processed_files or []
                    )

            # Create the agent execution record (pending state, aligned with
            # workflow behavior)
            agent_exec_repo = AgentExecutionRepository(self.db)
            app_obj = await self._db_get(App, config.app_id)
            agent_execution = AgentExecution(
                app_id=config.app_id,
                conversation_id=conversation_id,
                message_id=None,
                agent_config_id=config.id,
                release_id=app_obj.current_release_id if app_obj else None,
                triggered_by=None,
                steps=[],
                status="running",
                started_at=parse_timestamp_to_utc_naive(start_time),
                meta_data={
                    "model": api_key_obj.model_name,
                    "provider": api_key_obj.provider,
                },
            )
            agent_execution_id = await self._create_agent_execution(agent_exec_repo, agent_execution)

            try:
                # Run the agent (multimodal supported)
                result = await agent.chat(
                    message=llm_message,
                    history=history,
                    context=None,
                    files=processed_files
                )
            except Exception as e:
                # Agent failed: mark the execution record failed
                elapsed_time = time.time() - start_time
                await self._update_agent_execution(
                    agent_exec_repo,
                    execution_id=agent_execution_id,
                    steps=[],
                    status="failed",
                    elapsed_time=elapsed_time,
                    error_message=str(e)[:2000],
                )
                raise

        await self._record_api_key_usage(api_key_obj.id)

        elapsed_time = time.time() - start_time

        # suggested_questions
        suggested_questions = []
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if isinstance(sq_config, dict) and sq_config.get("enabled"):
            suggested_questions = await self.agent_service._generate_suggested_questions(
                features_config, result["content"],
                model_view, {}
            )

        audio_url = await self.agent_service._generate_tts(
            features_config, result["content"],
            {"model_name": api_key_obj.model_name, "api_key": api_key_obj.api_key,
             "api_base": api_key_obj.api_base, "provider": api_key_obj.provider},
            tenant_id=tenant_id, workspace_id=workspace_id
        )

        # Merge sandbox citations into the collector
        if _sandbox_adapter is not None:
            sandbox_cits = getattr(_sandbox_adapter, "_sandbox_citations", [])
            if sandbox_cits:
                seen_ids = {c.get("document_id") for c in citations_collector}
                for cit in sandbox_cits:
                    doc_id = cit.get("document_id")
                    if doc_id and doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        citations_collector.append(Citation(
                            document_id=str(doc_id),
                            doc_id=cit.get("doc_id", ""),
                            file_name=cit.get("file_name", ""),
                            knowledge_id=str(cit.get("knowledge_id", "")),
                            score=cit.get("score", 0),
                        ))

        # Filter citations (called once)
        filtered_citations = self.agent_service._filter_citations(features_config, citations_collector)

        # Build the user message content (multimodal files included)
        human_meta = {
            "files": [],
            "history_files": {}
        }
        assistant_meta = {
            "model": api_key_obj.model_name,
            "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
            "audio_url": None,
            "citations": filtered_citations,
            "suggested_questions": suggested_questions,
            "reasoning_content": result.get("reasoning_content")
        }
        if files:
            local_ids = [f.upload_file_id for f in files
                         if f.transfer_method.value == "local_file" and f.upload_file_id
                         and (not f.name or not f.size)]
            meta_map = {}
            if local_ids:
                meta_map = await self._fetch_completed_file_metadata(local_ids)
            for f in files:
                name, size = f.name, f.size
                if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                    meta = meta_map.get(str(f.upload_file_id))
                    if meta:
                        name = name or meta.file_name
                        size = size or meta.file_size
                human_meta["files"].append(serialize_file_reference(f, name=name, size=size))

        if processed_files:
            human_meta["history_files"] = {
                "content": sanitize_processed_files_for_history(processed_files),
                "provider": api_key_obj.provider,
            }

        if audio_url:
            assistant_meta["audio_url"] = audio_url
        # Long-term memory writes are handled uniformly by
        # conversation_service.add_message → memory dispatch; the old
        # write_long_term path is no longer triggered here.
        if not skip_save:
            await self.conversation_service.add_message_async(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data=human_meta,
            )
            await self.conversation_service.add_message_async(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=result["content"],
                meta_data=assistant_meta,
            )
            await self.conversation_service.dispatch_memory_pair(
                conversation_id,
                user_message={"id": user_message_id, "content": message, "meta_data": human_meta, "should_memorize": memory},
                assistant_message={"id": message_id, "content": result["content"], "meta_data": assistant_meta, "should_memorize": memory},
                storage_type=storage_type or "neo4j",
                user_rag_memory_id=user_rag_memory_id or "",
            )
            if used_context_engine:
                _ctx_kwargs = dict(
                    features=features_config,
                    conversation_id=conversation_id,
                    current_provider=api_key_obj.provider,
                    legacy_max_history=current_settings().agent_max_history,
                    model_config_id=config.default_model_config_id,
                )
                async def _run_after_turn(kwargs=_ctx_kwargs):
                    async with get_async_db_context() as db2:
                        await ContextEngineManager(db2).after_app_turn(**kwargs)
                asyncio.create_task(_run_after_turn())
        else:
            new_msg = Message(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=result["content"],
                version=version,
                is_current=True,
                parent_message_id=parent_message_id,
                meta_data=assistant_meta,
            )
            self.db.add(new_msg)
            conv = await self._db_get(Conversation, conversation_id)
            if conv:
                conv.message_count += 1

            if self._uses_async_session():
                await self.db.commit()
            else:
                self.db.commit()
        # Update the agent execution record to completed
        node_executions = orchestrator_node_executions + result.get("node_executions", [])
        await self._update_agent_execution(
            agent_exec_repo,
            execution_id=agent_execution_id,
            steps=node_executions,
            status="completed",
            elapsed_time=elapsed_time,
            token_usage=result.get("usage"),
            message_id=message_id,
        )

        return {
            "conversation_id": conversation_id,
            "message_id": str(message_id),
            "user_message_id": str(user_message_id),
            "message": result["content"],
            "reasoning_content": result.get("reasoning_content"),
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time,
            "suggested_questions": suggested_questions,
            "citations": filtered_citations,
            "audio_url": audio_url,
            "audio_status": "pending" if audio_url else None
        }

    @bind_usage("app", "config.app_id")
    async def agent_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            files: list[FileInput],
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            workspace_id: Optional[str] = None,
            source: str = "",
            history: Optional[List[Dict[str, Any]]] = None,
            skip_save: bool = False,
            parent_message_id: Optional[uuid.UUID] = None,
            version: int = 1,
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
    ) -> AsyncGenerator[str, None]:
        """Chat (streaming)."""

        save_messages_enqueued = False

        try:
            memory_trace = MessageTrace()
            start_time = time.time()
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()

            # Annotation hit check
            from ..models.annotation_model import HitLogSource
            annotation_match = await self._check_annotation_match_async(
                config.app_id,
                message,
                source=source or HitLogSource.EXTERNAL
            )
            if annotation_match:
                await self.conversation_service.add_message_async(
                    message_id=user_message_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    meta_data={"files": []}
                )
                await self.conversation_service.add_message_async(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=annotation_match["answer"],
                    meta_data={"usage": {}}
                )
                # messages-memory-decoupling Phase 2: memory dispatch converges
                # on the shared helper.
                await self.conversation_service.dispatch_memory_pair(
                    conversation_id,
                    user_message={"id": user_message_id, "content": message, "meta_data": {"files": []}},
                    assistant_message={"id": message_id, "content": annotation_match["answer"], "meta_data": {"usage": {}}},
                    storage_type=storage_type or "neo4j",
                    user_rag_memory_id=user_rag_memory_id or "",
                )
                yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"
                yield f"event: message\ndata: {json.dumps({'content': annotation_match['answer'], 'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"
                yield f"event: end\ndata: {json.dumps({'elapsed_time': time.time() - start_time, 'message_length': len(annotation_match['answer']), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}, ensure_ascii=False)}\n\n"
                return

            # Apply features config
            features_config: dict = config.features or {}
            if hasattr(features_config, 'model_dump'):
                features_config = features_config.model_dump()
            web_search_feature = features_config.get("web_search", {})
            if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
                web_search = False

            # Validate file upload
            self.agent_service._validate_file_upload(features_config, files)

            # Emotion-aware reply (App switch; returns None when off, and no
            # emotion processing happens at all). Started early so it runs
            # concurrently with prompt / context-engine / multimodal prep
            # instead of adding serially to time-to-first-token.
            emotion_detection = start_emotion_detection(features_config, message)

            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"

            variables = self.agent_service.prepare_variables(variables, config.variables)
            # Resolve the model config id
            model_config_id = config.default_model_config_id
            tenant_id = await self._resolve_tenant_id_async(workspace_id)
            api_key_obj = await ModelApiKeyService.resolve_runtime_api_key_bridge_or_raise_async(
                self.db,
                model_config_id,
                tenant_id=tenant_id,
            )
            if not api_key_obj:
                await ModelConfigService.raise_model_unavailable_bridge_async(
                    self.db, model_config_id, tenant_id=tenant_id
                )
            # Remote mode (G3): the non-decrypted view feeds the invoke seam;
            # credentials still serve sandbox/multimodal/usage consumers.
            model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db,
                model_config_id,
                tenant_id=tenant_id,
            )
            # Render system prompt (variable substitution supported)
            system_prompt = config.system_prompt
            if variables:
                system_prompt_rendered = render_prompt_message(
                    system_prompt,
                    PromptMessageRole.USER,
                    variables
                )
                system_prompt = system_prompt_rendered.get_text_content() or system_prompt

            # Load tools/skills/knowledge/memory concurrently (each opens its
            # own DB session, no cross-dependence)
            tools = []
            _ws_id = uuid.UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
            coros = [
                self.agent_service.load_tools_config(config.tools, web_search, tenant_id, user_id, workspace_id),
                self.agent_service.load_skill_config(config.skills, message, tenant_id, user_id, workspace_id),
                self.agent_service.load_knowledge_retrieval_config(
                    config.knowledge_retrieval,
                    user_id,
                    app_id=config.app_id,
                    workspace_id=_ws_id,
                    source=KnowledgeRetrievalSource.AGENT,
                ),
            ]
            if memory:
                coros.append(
                    self.agent_service.load_memory_config(config.memory, user_id, _ws_id, storage_type, user_rag_memory_id)
                )
            parallel_results = await asyncio.gather(*coros, return_exceptions=True)

            base_tools = parallel_results[0]
            skill_result = parallel_results[1]
            kb_result = parallel_results[2]
            memory_result = parallel_results[3] if memory else None

            if not isinstance(base_tools, Exception):
                tools.extend(base_tools)
            else:
                logger.warning("load_tools_config failed: %s", base_tools)

            if not isinstance(skill_result, Exception) and skill_result is not None:
                skill_tools, skill_prompts = skill_result
                tools.extend(skill_tools)
                if skill_prompts:
                    system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            elif isinstance(skill_result, Exception):
                logger.warning("load_skill_config failed: %s", skill_result)

            if not isinstance(kb_result, Exception) and kb_result is not None:
                kb_tools, citations_collector = kb_result
                tools.extend(kb_tools)
            elif isinstance(kb_result, Exception):
                logger.warning("load_knowledge_retrieval_config failed: %s", kb_result)
                citations_collector = []

            if memory_result is not None and not isinstance(memory_result, Exception):
                memory_tools, _ = memory_result
                tools.extend(memory_tools)
            elif isinstance(memory_result, Exception):
                logger.warning("load_memory_config failed: %s", memory_result)

            # Resolve model parameters
            model_parameters = config.model_parameters
            async def load_annotation_context():
                return await self._load_annotation_context_evidence(config.app_id, message)

            system_prompt = append_external_context_rule(system_prompt)

            # Load history (opening statement included)
            used_context_engine = False
            if history is None:
                context_engine_manager = ContextEngineManager(self.db)
                prepared_input = await context_engine_manager.prepare_app_agent_input(
                    features=features_config,
                    conversation_id=conversation_id,
                    system_prompt=system_prompt,
                    current_input=message,
                    current_provider=api_key_obj.provider,
                    legacy_max_history=current_settings().agent_max_history,
                    model_config_id=config.default_model_config_id,
                )
                if prepared_input:
                    system_prompt, history = prepared_input
                    used_context_engine = True
                else:
                    history = await self.conversation_service.get_conversation_history(
                        conversation_id=conversation_id,
                        max_history=current_settings().agent_max_history,
                        current_provider=api_key_obj.provider,
                    )

            # New-conversation opening statement is first stitched into the
            # in-memory history, avoiding a write + re-read before the first
            # chunk.
            is_new_conversation = not await self._conversation_has_messages(conversation_id)
            opening_statement = None
            opening_suggested_questions: List[str] = []
            if is_new_conversation:
                opening_statement, opening_suggested_questions = self.agent_service._get_opening_statement(
                    features_config, True, variables
                )
                if opening_statement:
                    history = self._append_history_message(
                        history,
                        role="assistant",
                        content=opening_statement,
                    )

            # Process multimodal files
            processed_files = None
            # User message sent to the LLM only (image list appended); the
            # stored original ``message`` stays untouched
            llm_message = message
            if files:
                multimodal_service = MultimodalService(self.db, model_view)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files,
                    document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id,
                    file_upload_config=fu_config if isinstance(fu_config, dict) else None,
                )
                logger.info(f"处理了 {len(processed_files)} 个文件")
                for tool in tools:
                    set_uploaded_files = getattr(tool, "set_uploaded_files", None)
                    if callable(set_uploaded_files):
                        set_uploaded_files(files)
                # List this turn's images and their numbering in the user
                # message so the model can explicitly trigger image retrieval
                image_manifest, _ = build_uploaded_images_manifest(files)
                if image_manifest:
                    llm_message = f"{message}\n\n{image_manifest}"
                if doc_img_recognition and Modality.IMAGE in (api_key_obj.input_modalities or []) and any(
                    f.type == FileType.DOCUMENT for f in files
                ):
                    system_prompt += (
                        "\n\n文档文字中可能包含图片位置标记如 [图片 第2页 第1张]。"
                        "对应图片已作为独立视觉输入提供，请结合位置标记和视觉内容理解文档；"
                        "不要在回答中输出任何文件地址、存储路径或内部标识。"
                    )

            # Emotion-aware reply: wait for the detection result → on success
            # write cache and inject "fixed principles + this turn's strategy";
            # on failure/miss the prompt stays untouched. Must complete before
            # ToolOrchestrator / LangChainAgent.
            # write_cache=memory: when memory is off the dispatcher fans out no
            # fast write, the cache has no consumer — skip to avoid orphan keys.
            system_prompt = await apply_emotion_detection(
                system_prompt, emotion_detection, user_message_id, write_cache=memory
            )

            # Weak model: drive multi-round tool calls with a ReAct prompt and
            # inject the trace into the system prompt.
            features = [str(item) for item in (api_key_obj.features or [])]
            orchestrator_node_executions = []
            _api_key_config = {
                "model_name": api_key_obj.model_name,
                "api_key": api_key_obj.api_key,
                "provider": api_key_obj.provider,
                "api_base": api_key_obj.api_base,
                "input_modalities": [str(item) for item in (api_key_obj.input_modalities or [])],
                "output_modalities": [str(item) for item in (api_key_obj.output_modalities or [])],
                "features": features,
                # Legacy fields kept for the sandbox payload contract only
                # (frozen until e2b-infra retires in the same batch)
                "is_omni": api_key_obj.is_omni,
                "capability": [str(item) for item in (api_key_obj.capability or [])],
                "tenant_id": api_key_obj.tenant_id,
                "model_config_id": api_key_obj.model_config_id,
                "channel_id": api_key_obj.channel_id,
            }
            use_agent_mode = ModelFeature.FUNCTION_CALL in features
            if not use_agent_mode and tools:
                stage_context = memory_stage_capture() if execution_mode == "in_process" else nullcontext()
                with stage_context:
                    system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                        tools=tools,
                        system_prompt=system_prompt,
                        message=message,
                        history=history,
                        model_view=model_view,
                        effective_params=model_parameters,
                        processed_files=processed_files,
                        context_evidence_loader=load_annotation_context,
                    )
                # Re-emit the already-completed tool-call steps to the client
                for step in orchestrator_node_executions:
                    event_type = "tool_error" if step.get("status") == "failed" else "tool_end"
                    memory_stages = []
                    if execution_mode == "in_process" and step.get("node_name") == "long_term_memory":
                        memory_stages = memory_trace.record_weak_tool(step)
                    yield f"event: tool_start\ndata: {json.dumps({'step_id': step.get('step_id'), 'name': step.get('node_name'), 'input': step.get('input'), 'meta': step.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                    for stage in memory_stages:
                        stage_event = {
                            "step_id": step.get("step_id"),
                            "stage": stage.get("stage"),
                            "status": stage.get("status", "completed"),
                            "data": stage.get("data") or {},
                        }
                        yield f"event: memory_stage\ndata: {json.dumps(stage_event, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                    yield f"event: {event_type}\ndata: {json.dumps({'step_id': step.get('step_id'), 'name': step.get('node_name'), 'output': step.get('output'), 'error': step.get('error'), 'meta': step.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                tools = []

            # Branch: sandbox vs in-process execution
            _sandbox_adapter = None
            if execution_mode == "sandbox":
                from .e2b_agent_adapter import E2BAgentAdapter

                _ws_id = uuid.UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
                sandbox_payload = await self.agent_service._build_sandbox_payload(
                    agent_config=config,
                    model_config=None,
                    api_key_config=_api_key_config,
                    effective_params=model_parameters,
                    message=message,
                    system_prompt=system_prompt,
                    workspace_id=_ws_id,
                    user_id=user_id,
                    conversation_id=str(conversation_id),
                    tools=tools,
                    history=history,
                    context=None,
                    variables=variables,
                    files_config=features_config.get("file_upload", {}),
                )
                _sandbox_adapter = E2BAgentAdapter(self.db)
                _chunk_stream = self.agent_service._sandbox_event_stream(
                    payload=sandbox_payload,
                    workspace_id=str(_ws_id),
                    user_id=user_id or "",
                    conversation_id=str(conversation_id),
                    adapter=_sandbox_adapter,
                )
            else:
                # Create the LangChain Agent (remote mode: identity/capability
                # facts come from the non-decrypted view; credentials are never
                # sent upstream)
                agent = LangChainAgent(
                    model_name=api_key_obj.model_name,
                    model_view=model_view,
                    provider=api_key_obj.provider,
                    temperature=model_parameters.get("temperature", 0.7),
                    max_tokens=model_parameters.get("max_tokens", 2000),
                    system_prompt=system_prompt,
                    tools=tools,
                    streaming=True,
                    deep_thinking=model_parameters.get("deep_thinking", False),
                    thinking_budget_tokens=model_parameters.get("thinking_budget_tokens"),
                    json_output=model_parameters.get("json_output", False),
                    context_query=message,
                    context_base_text=system_prompt + "\n" + str(history) + "\n" + message,
                    context_evidence_loader=load_annotation_context,
                )

                # Inject runtime context into tools that need it
                for t in tools:
                    if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                        t.tool_instance.set_runtime_context(
                            user_id=user_id or "anonymous",
                            conversation_id=str(conversation_id) if conversation_id else None,
                            uploaded_files=processed_files or []
                        )

                _chunk_stream = agent.chat_stream(
                    message=llm_message,
                    history=history,
                    context=None,
                    files=processed_files,
                )

            # Pre-read ORM attributes before close() to avoid
            # DetachedInstanceError afterwards
            _api_key_id = api_key_obj.id
            _api_key_model_name = api_key_obj.model_name
            _api_key_provider = api_key_obj.provider
            # Pre-read _release_id so no DB connection is re-acquired after the
            # LLM inference finishes
            _app_obj = await self._db_get(App, config.app_id)
            _release_id = _app_obj.current_release_id if _app_obj else None
            # The shared session is no longer needed during inference — hand
            # the underlying connection back to the pool early.
            await self._release_db_connection()

            # Stream from the Agent (multimodal supported) with TTS running in
            # parallel
            full_content = ""
            full_reasoning = ""
            total_tokens = 0
            node_executions = []

            text_queue: asyncio.Queue = asyncio.Queue()
            api_key_config = {
                "model_name": api_key_obj.model_name,
                "api_key": api_key_obj.api_key,
                "api_base": api_key_obj.api_base,
                "provider": api_key_obj.provider,
            }
            stream_audio_url, tts_task = await self.agent_service._generate_tts_streaming(
                features_config, api_key_config,
                text_queue=text_queue,
                tenant_id=tenant_id, workspace_id=workspace_id
            )

            capture_memory_stages = execution_mode == "in_process"
            if capture_memory_stages:
                _chunk_stream = capture_memory_stage_stream(_chunk_stream)
            async for chunk in _chunk_stream:
                if isinstance(chunk, int):
                    total_tokens = chunk
                elif isinstance(chunk, dict) and chunk.get("type") == "reasoning":
                    full_reasoning += chunk['content']
                    yield f"event: reasoning\ndata: {json.dumps({'content': chunk['content']}, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "node_executions":
                    node_executions = chunk.get("data", [])
                    if capture_memory_stages:
                        memory_trace.reconcile_tools(node_executions)
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_start":
                    if capture_memory_stages:
                        memory_trace.start_tool(
                            step_id=chunk.get("step_id"),
                            name=chunk.get("name", ""),
                            input_value=chunk.get("input"),
                        )
                    yield f"event: tool_start\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'input': chunk.get('input'), 'meta': chunk.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif (
                    capture_memory_stages
                    and isinstance(chunk, dict)
                    and chunk.get("type") == "memory_stage"
                ):
                    payload = memory_trace.add_stage(step_id=chunk.get("step_id"), stage=chunk)
                    if payload is None:
                        continue
                    stage_event = {"step_id": chunk.get("step_id"), **payload}
                    yield f"event: memory_stage\ndata: {json.dumps(stage_event, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_end":
                    if capture_memory_stages:
                        memory_trace.finish_tool(step_id=chunk.get("step_id"), status="completed")
                    yield f"event: tool_end\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'output': chunk.get('output'), 'meta': chunk.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_error":
                    if capture_memory_stages:
                        memory_trace.finish_tool(
                            step_id=chunk.get("step_id"),
                            status="failed",
                            error=chunk.get("error"),
                        )
                    yield f"event: tool_error\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'error': chunk.get('error')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "agent_log":
                    yield f"event: agent_log\ndata: {json.dumps(chunk, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, str):
                    full_content += chunk
                    yield f"event: message\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
                    if tts_task is not None:
                        await text_queue.put(chunk)
                elif isinstance(chunk, dict):
                    event_type = str(chunk.get("type") or "unknown")
                    yield f"event: {event_type}\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            if tts_task is not None:
                await text_queue.put(None)

            # Merge sandbox citations into collector
            if _sandbox_adapter is not None:
                sandbox_cits = getattr(_sandbox_adapter, "_sandbox_citations", [])
                if sandbox_cits:
                    seen_ids = {c.get("document_id") for c in citations_collector}
                    for cit in sandbox_cits:
                        doc_id = cit.get("document_id")
                        if doc_id and doc_id not in seen_ids:
                            seen_ids.add(doc_id)
                            citations_collector.append(Citation(
                                document_id=str(doc_id),
                                doc_id=cit.get("doc_id", ""),
                                file_name=cit.get("file_name", ""),
                                knowledge_id=str(cit.get("knowledge_id", "")),
                                score=cit.get("score", 0),
                            ))

            elapsed_time = time.time() - start_time
            await self._record_api_key_usage(_api_key_id)

            # Emit the end event (suggested_questions, tts, audio_status,
            # citations included)
            end_data: dict = {"elapsed_time": elapsed_time, "message_length": len(full_content), "error": None}
            sq_config = features_config.get("suggested_questions_after_answer", {})
            suggested_questions = []
            if isinstance(sq_config, dict) and sq_config.get("enabled"):
                suggested_questions = await self.agent_service._generate_suggested_questions(
                    features_config, full_content,
                    model_view, {}
                )
                end_data["suggested_questions"] = suggested_questions
            end_data["audio_url"] = stream_audio_url
            # Check whether TTS already finished (non-blocking, no cancel)
            audio_status = "pending"
            if tts_task is not None and tts_task.done():
                # Task finished — check for an exception
                try:
                    tts_task.result()
                    audio_status = "completed"
                except Exception as e:
                    logger.warning(f"TTS任务异常: {e}")
                    audio_status = "failed"
            end_data["audio_status"] = audio_status if stream_audio_url else None
            # Filter citations (single call)
            filtered_citations = self.agent_service._filter_citations(features_config, citations_collector)
            end_data["citations"] = filtered_citations

            human_meta = {
                "files": [],
                "history_files": {}
            }
            assistant_meta = {
                "model": _api_key_model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                "audio_url": stream_audio_url,
                "citations": filtered_citations,
                "suggested_questions": suggested_questions,
                "reasoning_content": full_reasoning or None
            }
            assistant_meta = merge_memory_trace(assistant_meta, memory_trace)

            # Long-term memory writes are taken over uniformly by
            # conversation_service.add_message → MemoryWriteDispatcher; the old
            # write_long_term path is no longer triggered here.
            if files:
                local_ids = [f.upload_file_id for f in files
                             if f.transfer_method.value == "local_file" and f.upload_file_id
                             and (not f.name or not f.size)]
                meta_map = await self._fetch_completed_file_metadata(local_ids) if local_ids else {}
                for f in files:
                    name, size = f.name, f.size
                    if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                        meta = meta_map.get(str(f.upload_file_id))
                        if meta:
                            name = name or meta.file_name
                            size = size or meta.file_size
                    human_meta["files"].append(serialize_file_reference(f, name=name, size=size))
            if processed_files:
                human_meta["history_files"] = {
                    "content": sanitize_processed_files_for_history(processed_files),
                    "provider": _api_key_provider,
                }

            all_node_executions = [
                strip_memory_trace_transients(node)
                for node in (orchestrator_node_executions + node_executions)
            ]
            if not skip_save:
                from .batch_persist_queue import BatchPersistQueue, PersistTask

                persist_args = {
                    "conversation_id": str(conversation_id),
                    "user_message_id_override": user_message_id,
                    "user_message_content": message,
                    # The user message attaches to the version the user is currently
                    # viewing, so a version switch isolates follow-up turns by parent
                    "user_parent_message_id": await self._get_last_current_assistant_id(
                        conversation_id
                    ),
                    "files_meta": human_meta.get("files", []),
                    "api_key_model_name": _api_key_model_name,
                    "should_memorize": memory,
                }
                if opening_statement:
                    opening_id = uuid.uuid4()
                    persist_args["is_new_conversation"] = True
                    persist_args["opening_statement"] = opening_statement
                    persist_args["opening_message_id"] = opening_id
                    persist_args["opening_suggested_questions"] = opening_suggested_questions

                # Build a StreamResult-alike accumulator for the persist task
                from .chat_context import StreamResult as _StreamResult
                _result = _StreamResult()
                _result.message_id = message_id
                _result.user_message_id = user_message_id
                _result.full_content = full_content
                _result.total_tokens = total_tokens
                _result.full_reasoning = full_reasoning
                _result.suggested_questions = suggested_questions
                _result.citations = filtered_citations
                _result.audio_url = stream_audio_url
                _result.audio_status = audio_status if stream_audio_url else None
                _result.history_files = human_meta.get("history_files")
                _result.assistant_meta = assistant_meta
                persist_args["result"] = _result

                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="save_messages",
                    args=persist_args,
                ))
                save_messages_enqueued = True

                # messages-memory-decoupling Phase 2: memory dispatch converges
                # on the shared helper; a single write_batch assigns consecutive
                # seq, removing the seq inversion of the previous two
                # fire-and-forget dispatches.
                await self.conversation_service.dispatch_memory_pair(
                    conversation_id,
                    user_message={"id": user_message_id, "content": message, "meta_data": human_meta, "should_memorize": memory},
                    assistant_message={"id": message_id, "content": full_content, "meta_data": assistant_meta, "should_memorize": memory},
                    storage_type=storage_type or "neo4j",
                    user_rag_memory_id=user_rag_memory_id or "",
                )

                # Enqueue agent execution after messages so the FK is satisfied
                # within the same batch (messages commit first, then execution).
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="save_agent_execution",
                    args={
                        "app_id": str(config.app_id),
                        "conversation_id": str(conversation_id),
                        "message_id": str(message_id),
                        "agent_config_id": str(config.id) if config.id else None,
                        "release_id": str(_release_id) if _release_id else None,
                        "steps": all_node_executions,
                        "status": "completed",
                        "started_at_ts": start_time,
                        "elapsed_time": elapsed_time,
                        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                        "meta_data": {"model": _api_key_model_name, "provider": _api_key_provider},
                    },
                ))

                if used_context_engine:
                    await BatchPersistQueue.enqueue(PersistTask(
                        task_type="after_turn",
                        args={
                            "conversation_id": str(conversation_id),
                            "features_config": features_config,
                            "api_key_provider": _api_key_provider,
                            "model_config_id": str(config.default_model_config_id) if config.default_model_config_id else None,
                        },
                    ))
            else:
                async with get_async_db_context() as db:
                    new_msg = Message(
                        id=message_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_content,
                        version=version,
                        is_current=True,
                        parent_message_id=parent_message_id,
                        meta_data=assistant_meta,
                    )
                    db.add(new_msg)
                    conv = await db.get(Conversation, conversation_id)
                    if conv:
                        conv.message_count += 1
                    await db.commit()
                # The assistant message is already written directly; mark it so
                # the exception branch does not enqueue save_failed_message and
                # collide on the same message_id primary key
                save_messages_enqueued = True
                from .batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="save_agent_execution",
                    args={
                        "app_id": str(config.app_id),
                        "conversation_id": str(conversation_id),
                        "message_id": str(message_id),
                        "agent_config_id": str(config.id) if config.id else None,
                        "release_id": str(_release_id) if _release_id else None,
                        "steps": all_node_executions,
                        "status": "completed",
                        "started_at_ts": start_time,
                        "elapsed_time": elapsed_time,
                        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                        "meta_data": {"model": _api_key_model_name, "provider": _api_key_provider},
                    },
                ))

            yield f"event: end\ndata: {json.dumps(end_data, ensure_ascii=False)}\n\n"

            logger.info(
                "流式聊天完成",
                extra={
                    "conversation_id": str(conversation_id),
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except (GeneratorExit, asyncio.CancelledError):
            # Generator closed or task cancelled — normal exit
            logger.debug("流式聊天被中断")
            raise
        except Exception as e:
            # The share-chat Agent stream does not go through
            # AgentRunService.run_stream, so multimodal error classification is
            # filled in at this unified exception boundary.
            from ..llm_error_handler import classify_multimodal_exception

            debug_id = self.agent_service._build_debug_id()
            public_error = classify_multimodal_exception(e, debug_id=debug_id)
            if public_error is not None:
                display_error = public_error["message"]
            elif isinstance(e, BusinessException):
                # Business exceptions carry user-facing text; str(e) would
                # prefix the internal error code
                display_error = e.message
            else:
                display_error = str(e)

            if public_error is not None:
                logger.error(
                    "流式聊天多模态输入失败（已安全处理）",
                    extra={
                        "error_type": type(e).__name__,
                        "error_kind": public_error["kind"],
                        "debug_id": debug_id,
                    },
                    exc_info=True,
                )
            else:
                logger.error(f"流式聊天失败: {str(e)}", exc_info=True)

            # Save the failed message so the front end can render a failed
            # state. Note: once save_messages is enqueued, do not enqueue
            # save_failed_message — the identical message_id would collide on
            # the messages primary key.
            if not save_messages_enqueued:
                try:
                    from .batch_persist_queue import BatchPersistQueue, PersistTask
                    await BatchPersistQueue.enqueue(PersistTask(
                        task_type="save_failed_message",
                        args={
                            "conversation_id": str(conversation_id) if 'conversation_id' in locals() else "",
                            "user_message_id": str(user_message_id) if 'user_message_id' in locals() else str(uuid.uuid4()),
                            "message_id": str(message_id) if 'message_id' in locals() else str(uuid.uuid4()),
                            "user_message_content": message if 'message' in locals() else "",
                            "files_meta": [],
                            "error_message": display_error,
                            "error_detail": str(e)[:2000],
                        },
                    ))
                except Exception:
                    pass
            # Failure paths also persist in a single tail write instead of
            # relying on execution before the first chunk.
            try:
                from .batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="save_agent_execution",
                    args={
                        "app_id": str(config.app_id),
                        "conversation_id": str(conversation_id),
                        "message_id": None,
                        "agent_config_id": str(config.id) if config.id else None,
                        "release_id": None,
                        "steps": node_executions if 'node_executions' in locals() else [],
                        "status": "failed",
                        "started_at_ts": start_time,
                        "elapsed_time": time.time() - start_time,
                        "error_message": str(e)[:2000],
                        "meta_data": {
                            "model": _api_key_model_name if '_api_key_model_name' in locals() else None,
                            "provider": _api_key_provider if '_api_key_provider' in locals() else None,
                        },
                    },
                ))
            except Exception:
                pass  # persist failure must not block the error event
            # Keep the share page's existing end/error string contract so the
            # front end never renders a raw object.
            yield f"event: end\ndata: {json.dumps({'error': display_error}, ensure_ascii=False)}\n\n"

    @staticmethod
    def _format_sse_chunk(chunk: dict) -> str:
        """Format a dict chunk as an SSE event string."""
        event_type = str(chunk.get("type") or "unknown")
        return f"event: {event_type}\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    @bind_usage("app", "config.app_id")
    async def multi_agent_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: MultiAgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            thinking: bool = False,
            execution_mode: Optional[str] = None,
            files: Optional[List[FileInput]] = None,
    ) -> Dict[str, Any]:
        """Multi-agent chat (non-streaming).

        thinking / execution_mode / files are optional additions aligned with
        Agent apps; absent = the previous behavior.
        """

        start_time = time.time()
        user_message_id = uuid.uuid4()
        # S1 turn anchor: the assistant message id is pre-generated so the
        # master execution record can link to this turn's message; log detail
        # attaches nodes by message instead of relying on time-order proximity
        # (which scatters across messages in multi-turn scenarios).
        message_id = uuid.uuid4()
        actual_config_id = None
        config_id = actual_config_id

        if variables is None:
            variables = {}

        # Annotation hit (by app_id via AppAnnotationSetting; unconfigured /
        # disabled returns None, no impact on legacy clusters). A hit bypasses
        # the orchestrator and persists the annotation answer directly (same
        # as Agent apps).
        _annotation_hit = await self._cluster_annotation_hit(
            config, message, conversation_id, message_id, user_message_id
        )
        if _annotation_hit is not None:
            return {
                "conversation_id": conversation_id,
                "message": _annotation_hit["answer"],
                "message_id": str(message_id),
                "user_message_id": str(user_message_id),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "elapsed_time": time.time() - start_time,
            }

        # 2. Create the orchestrator
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        # New-conversation opening statement (queries only when
        # features.opening_statement is on; legacy clusters have empty features,
        # zero cost)
        opening_statement, opening_suggested_questions = await self._resolve_cluster_opening(
            orchestrator, conversation_id, variables
        )

        # 3. Execute the task
        result = await orchestrator.execute(
            message=message,
            conversation_id=conversation_id,
            user_id=user_id,
            variables=variables,
            use_llm_routing=True,  # LLM routing enabled by default
            web_search=web_search,  # web search switch
            memory=memory,  # memory switch
            # S5: these two arguments used to be dropped on the non-streaming
            # entry (collaboration mode therefore lost storage/memory context)
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            message_id=message_id,
            thinking=thinking,
            opening_statement=opening_statement,
            execution_mode=execution_mode,
            files=files or None,  # attachments (absent/empty = previous behavior)
            user_message_id=user_message_id,  # emotion cache key (features.emotion_reply only)
        )

        elapsed_time = time.time() - start_time

        # The new-conversation opening statement lands first as the first
        # assistant message (same as Agent non-streaming)
        if opening_statement:
            await self.conversation_service.add_message_async(
                conversation_id=conversation_id,
                role="assistant",
                content=opening_statement,
                meta_data={"suggested_questions": opening_suggested_questions},
            )

        # Persist messages
        # Attachments persist too (same as Agent apps); without files no
        # meta_data is passed, so legacy-cluster writes are unchanged
        _user_meta_kwargs: Dict[str, Any] = {}
        _user_meta: Dict[str, Any] = {}
        if files:
            _files_meta, _history_files = await self._build_cluster_files_persist(files, orchestrator)
            _user_meta = {"files": _files_meta}
            if _history_files:
                _user_meta["history_files"] = _history_files
        # This turn's variable values persist with the user message so
        # regeneration can read them back (no key when empty: legacy unchanged)
        if variables:
            _user_meta.setdefault("files", [])
            _user_meta["variables"] = variables
        if _user_meta:
            _user_meta_kwargs["meta_data"] = _user_meta
        # The user message attaches to the version the user is currently
        # viewing (must be queried before this turn's messages are written)
        _user_parent_id = await self._get_last_current_assistant_id(conversation_id)
        await self.conversation_service.add_message_async(
            message_id=user_message_id,
            conversation_id=conversation_id,
            role="user",
            content=message,
            parent_message_id=_user_parent_id,
            **_user_meta_kwargs,
        )

        # Conversation-capability extras (suggested questions / citations /
        # audio): present on result only when the corresponding features are
        # on; legacy clusters have empty _cluster_extras, so both the persist
        # payload and the return value stay unchanged
        _cluster_extras: Dict[str, Any] = {
            _k: result[_k]
            for _k in ("suggested_questions", "citations", "audio_url", "audio_status")
            if _k in result
        }
        ai_message = await self.conversation_service.add_message_async(
            message_id=message_id,
            conversation_id=conversation_id,
            role="assistant",
            content=result.get("message", ""),
            meta_data={
                "mode": result.get("mode"),
                "elapsed_time": result.get("elapsed_time"),
                "usage": result.get("usage", {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }),
                **_cluster_extras,
            }
        )

        # Context-engine after_turn (enqueued only when this turn actually ran
        # features.context_engine; legacy clusters always get None)
        _ctx_after_turn = orchestrator.context_engine_after_turn_args(conversation_id)
        if _ctx_after_turn:
            try:
                from .batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(task_type="after_turn", args=_ctx_after_turn))
            except Exception as e:
                logger.warning("入队 context_engine after_turn 失败（不影响对话）", extra={"error": str(e)})

        # S1 turn anchor: after messages persist, backfill the master execution
        # record's message_id (idempotent) so log detail attaches nodes exactly
        # to this turn's assistant message.
        _master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if _master_execution_id is not None:
            try:
                from .batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(_master_execution_id),
                        "message_id": str(message_id),
                    },
                ))
            except Exception as e:
                logger.warning("回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)})

        return {
            "conversation_id": conversation_id,
            "message": result.get("message", ""),
            "message_id": str(ai_message.id),
            "user_message_id": str(user_message_id),
            # S4: non-streaming now returns the real usage (orchestrator
            # execute() switched to the ledger accounting) instead of a
            # constant zero.
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time,
            # Suggested questions / citations / audio: orchestrator carries
            # these keys only when the corresponding features are on; legacy
            # clusters return the same shape as before
            **{k: result[k] for k in ("suggested_questions", "citations", "audio_url", "audio_status") if k in result},
        }

    @bind_usage("app", "config.app_id")
    async def multi_agent_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: MultiAgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            thinking: bool = False,
            execution_mode: Optional[str] = None,
            files: Optional[List[FileInput]] = None,
    ) -> AsyncGenerator[str, None]:
        """Multi-agent chat (streaming).

        thinking / execution_mode are optional additions aligned with Agent
        apps; absent = the previous behavior.
        """

        start_time = time.time()

        if variables is None:
            variables = {}

        save_messages_enqueued = False

        try:
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()
            # Emit the start event
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"

            full_content = ""
            total_tokens = 0
            # Conversation-capability extras (suggested questions / citations /
            # audio) carried by the end event; empty when features are off
            end_extras: Dict[str, Any] = {}

            # Annotation hit (unconfigured / disabled returns None, no impact
            # on legacy clusters); a hit bypasses the orchestrator, persists
            # directly and emits message/end (same as Agent apps).
            _annotation_hit = await self._cluster_annotation_hit(
                config, message, conversation_id, message_id, user_message_id,
                storage_type=storage_type, user_rag_memory_id=user_rag_memory_id,
            )
            if _annotation_hit is not None:
                _answer = _annotation_hit["answer"]
                yield f"event: message\ndata: {json.dumps({'content': _answer, 'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"
                yield f"event: end\ndata: {json.dumps({'elapsed_time': time.time() - start_time, 'conversation_id': str(conversation_id), 'message_id': str(message_id), 'message_length': len(_answer), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}, ensure_ascii=False)}\n\n"
                return

            # 2. Create the orchestrator
            orchestrator = await MultiAgentOrchestrator.create(self.db, config)

            # New-conversation opening statement (queries only when
            # features.opening_statement is on; legacy clusters have empty
            # features, zero cost)
            opening_statement, opening_suggested_questions = await self._resolve_cluster_opening(
                orchestrator, conversation_id, variables
            )

            # 3. Stream the task
            # message_id is passed down: the master execution record carries
            # this turn's assistant message_id, so log detail can attach
            # sub-agent nodes to that message without the "time proximity"
            # fallback.
            async for event in orchestrator.execute_stream(
                    message=message,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    variables=variables,
                    use_llm_routing=True,
                    web_search=web_search,  # web search switch
                    memory=memory,  # memory switch
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                    message_id=message_id,
                    thinking=thinking,
                    opening_statement=opening_statement,
                    execution_mode=execution_mode,
                    files=files or None,
                    user_message_id=user_message_id,  # emotion cache key (features.emotion_reply only)
            ):
                # S4: sub_usage strings are no longer intercepted to
                # accumulate tokens — the orchestrator's built-in per-turn
                # ledger (routing/sub/merge tiers) publishes the usage on the
                # end event (the single authoritative accounting); this layer
                # reads it from end for persistence.
                _event_name = ""
                if event.startswith("event:"):
                    _event_name = event[6:].split("\n", 1)[0].strip()

                if _event_name == "end" and "data:" in event:
                    try:
                        data_line = event.split("data: ", 1)[1].strip()
                        data = json.loads(data_line)
                        total_tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
                        # Conversation-capability extras (end carries them
                        # only when the corresponding features are on)
                        for _k in ("suggested_questions", "citations", "audio_url", "audio_status"):
                            if _k in data:
                                end_extras[_k] = data[_k]
                    except Exception:
                        pass

                yield event
                # Accumulate the main bubble body: only cluster-level
                # `message` events count. Sub-agent bodies use
                # `sub_agent_message` (shown in their own blocks); including
                # them would persist a duplicated body, making the main
                # bubble longer after a refresh.
                # Note: since S3 sub-agent events no longer carry the
                # sub-conversation conversation_id, this collection matches
                # the orchestrator's message-event publication 1:1
                # (supervisor=merge output, supervisor_loop=supervisor ReAct
                # body, collaboration=handoffs main reply).
                if _event_name == "message" and "data:" in event:
                    try:
                        data_line = event.split("data: ", 1)[1].strip()
                        data = json.loads(data_line)
                        if "content" in data:
                            full_content += data["content"]
                    except Exception:
                        pass

            elapsed_time = time.time() - start_time

            # Enqueue async batch persist
            # S3 persistence convergence: this entry (the app-orchestration
            # side) is the single persist layer — the persisted body is
            # full_content (the orchestrator message-event sequence = the
            # final main-bubble content the client shows), written uniformly
            # through BatchPersistQueue; MultiAgentService/SharedChatService
            # no longer assemble and store content themselves (the root cause
            # of multiple assistant rows per turn). For the supervisor
            # single-agent pass-through case the message event is the
            # sub-agent output itself, so persist and display use the same
            # content — eliminating "long-conversation return value is not
            # the final content".
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
            _persist_args = {
                "conversation_id": str(conversation_id),
                "result": _result,
                "user_message_id_override": user_message_id,
                "user_message_content": message,
                # The user message attaches to the version the user is currently
                # viewing, so a version switch isolates follow-up turns by parent
                "user_parent_message_id": await self._get_last_current_assistant_id(
                    conversation_id
                ),
                "should_memorize": orchestrator.cluster_memory_enabled(),
            }
            # This turn's variable values persist with the user message so
            # regeneration can read them back (no key when empty: legacy unchanged)
            if variables:
                _persist_args["variables_meta"] = variables
            # New-conversation opening statement persists too (same as Agent
            # apps); with no opening statement no new keys are added, so
            # legacy-cluster writes are unchanged
            if opening_statement:
                _persist_args["is_new_conversation"] = True
                _persist_args["opening_statement"] = opening_statement
                _persist_args["opening_message_id"] = uuid.uuid4()
                _persist_args["opening_suggested_questions"] = opening_suggested_questions
            # Attachments persist too (same as Agent apps); without files no
            # new keys are added, so legacy-cluster writes are unchanged
            if files:
                _files_meta, _history_files = await self._build_cluster_files_persist(files, orchestrator)
                _persist_args["files_meta"] = _files_meta
                if _history_files:
                    _result.history_files = _history_files
            # Suggested questions / citations / audio persist too (same as
            # Agent apps); when the corresponding features are off end_extras
            # is empty and no field is written
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
                args=_persist_args,
            ))
            save_messages_enqueued = True

            # Context-engine post-turn summary (arguments exist only when the
            # turn actually ran the context engine; legacy clusters always get
            # None and nothing is enqueued)
            _ctx_after_args = orchestrator.context_engine_after_turn_args(conversation_id)
            if _ctx_after_args:
                await BatchPersistQueue.enqueue(PersistTask(task_type="after_turn", args=_ctx_after_args))

            # Master execution message_id backfill: the record was created at
            # stream start (sub-agent records need it as the parent FK) before
            # this turn's assistant message was persisted; writing message_id
            # then would violate the FK. Registered here after save_messages,
            # so the persist queue backfills once the message commits — log
            # detail then attaches nodes exactly by message (instead of the
            # time-proximity fallback).
            _master_execution_id = getattr(orchestrator, "current_execution_id", None)
            if _master_execution_id is not None:
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(_master_execution_id),
                        "message_id": str(message_id),
                    },
                ))

            logger.info(
                "多 Agent 流式聊天完成",
                extra={
                    "conversation_id": str(conversation_id),
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except (GeneratorExit, asyncio.CancelledError):
            # Generator closed or task cancelled — normal exit
            logger.debug("多 Agent 流式聊天被中断")
            raise
        except Exception as e:
            logger.error(f"多 Agent 流式聊天失败: {str(e)}", exc_info=True)
            # Once save_messages is enqueued, do not enqueue
            # save_failed_message — the identical message_id would collide on
            # the messages primary key.
            if not save_messages_enqueued:
                try:
                    from .batch_persist_queue import BatchPersistQueue, PersistTask
                    await BatchPersistQueue.enqueue(PersistTask(
                        task_type="save_failed_message",
                        args={
                            "conversation_id": str(conversation_id) if 'conversation_id' in locals() else "",
                            "user_message_id": str(user_message_id) if 'user_message_id' in locals() else str(uuid.uuid4()),
                            "message_id": str(message_id) if 'message_id' in locals() else str(uuid.uuid4()),
                            "user_message_content": message if 'message' in locals() else "",
                            "files_meta": [],
                            "error_message": "An error occurred during generation.",
                            "error_detail": str(e)[:2000],
                        },
                    ))
                except Exception:
                    pass
            # Emit the error event
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    async def workflow_chat(
            self,
            message: Optional[str],
            conversation_id: Optional[uuid.UUID],
            config: WorkflowConfig,
            app_id: uuid.UUID,
            release_id: uuid.UUID,
            workspace_id: uuid.UUID,
            files: Optional[List[FileInput]] = None,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            source: str = "",
    ) -> Dict[str, Any]:
        """Chat (non-streaming)."""
        payload = DraftRunRequest(
            message=message,
            variables=variables,
            conversation_id=str(conversation_id) if conversation_id else None,
            stream=True,
            user_id=user_id,
            files=files
        )
        return await self.workflow_service.run(
            app_id=app_id,
            payload=payload,
            config=config,
            workspace_id=workspace_id,
            release_id=release_id,
            source=source,
            prepared_memory_storage_type=storage_type,
            prepared_user_rag_memory_id=user_rag_memory_id,
        )

    async def workflow_chat_stream(
            self,
            message: Optional[str],
            conversation_id: Optional[uuid.UUID],
            config: WorkflowConfig,
            app_id: uuid.UUID,
            release_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: str = None,
            variables: Optional[Dict[str, Any]] = None,
            files: Optional[List[FileInput]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            public=False,
            source: str = "",

    ) -> AsyncGenerator[dict, None]:
        """Chat (streaming)."""
        payload = DraftRunRequest(
            message=message,
            variables=variables,
            conversation_id=str(conversation_id) if conversation_id else None,
            stream=True,
            user_id=user_id,
            files=files
        )
        async for event in self.workflow_service.run_stream(
                app_id=app_id,
                payload=payload,
                config=config,
                workspace_id=workspace_id,
                release_id=release_id,
                public=public,
                source=source,
                prepared_memory_storage_type=storage_type,
                prepared_user_rag_memory_id=user_rag_memory_id,
        ):
            yield event

    async def workflow_resume_intervention_stream(
            self,
            execution_id: str,
            app_id: uuid.UUID,
            node_id: str,
            action_id: str,
            form_data: Optional[Dict[str, Any]] = None,
            public: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Resume a waiting-human workflow via SSE stream (page refresh case)."""
        async for event in self.workflow_service.resume_intervention_stream(
            execution_id=execution_id,
            app_id=app_id,
            node_id=node_id,
            action_id=action_id,
            form_data=form_data,
            public=public,
        ):
            yield event

    # ==================== Regeneration ====================

    def _locate_or_restore_parent_user_message(self, original_msg: Message) -> Message:
        """Locate the parent user message of the original assistant message.

        Lookup order:
        1. The already-backfilled parent_message_id first (ignored when it
           points at a non-user message — dirty data);
        2. Otherwise trace back to the latest user message in the same
           conversation with created_at <= the original message (not filtered
           by is_deleted, since a deleted message was still this turn's actual
           question; <= instead of < tolerates same-millisecond user/assistant
           inserts);
        3. Still not found: fall back to the latest user message in the
           conversation regardless of time order (covers dirty data with
           abnormal created_at ordering).

        If the located parent is soft-deleted (the user accidentally deleted
        the original question before regenerating), restore it (is_deleted
        back to False) and continue instead of raising "original user message
        not found" — so an accidental deletion never makes the turn
        unregenerable.
        """
        from sqlalchemy import select
        parent_msg = None
        if original_msg.parent_message_id:
            candidate = self.db.get(Message, original_msg.parent_message_id)
            if candidate and candidate.role == "user":
                parent_msg = candidate
        if not parent_msg:
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at <= original_msg.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
        if not parent_msg:
            # Fallback: the latest user message in the conversation (ignoring
            # time order), covering dirty data with abnormal created_at
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
        if not parent_msg:
            raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
        # Backfill parent_message_id for later versioned linkage
        if original_msg.parent_message_id != parent_msg.id:
            original_msg.parent_message_id = parent_msg.id
            self.db.commit()
        if parent_msg.is_deleted:
            parent_msg.is_deleted = False
            self.db.commit()
            logger.info(
                "重新生成时自动恢复被删除的父 user 消息",
                extra={
                    "parent_message_id": str(parent_msg.id),
                    "assistant_message_id": str(original_msg.id),
                    "conversation_id": str(original_msg.conversation_id),
                },
            )
        return parent_msg

    async def regenerate(
            self,
            message_id: uuid.UUID,
            config,
            workspace_id: uuid.UUID,
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Regenerate a reply (non-streaming, multi-version).

        Args:
            message_id: the original assistant message id
            config: AgentConfig
            workspace_id: workspace id
            user_id: user id
            variables: variables
            web_search: web search switch
            memory: long-term memory switch
            storage_type: storage type
            user_rag_memory_id: RAG memory id

        Returns:
            Dict with the new message id, content, version, etc.
        """
        # 1. Load the original message
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. Locate the parent user message (trace by created_at when the
        # backfilled parent_message_id is missing; a soft-deleted parent is
        # auto-restored — see _locate_or_restore_parent_user_message)
        parent_msg = self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3. Query all versions under the same parent_message_id for the max
        # version
        from sqlalchemy import func
        max_version_result = self.db.scalars(
            select(func.max(Message.version))
            .where(
                Message.conversation_id == original_msg.conversation_id,
                Message.parent_message_id == parent_msg_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            )
        ).first()
        max_version = max_version_result or 0
        new_version = max_version + 1

        # 4. Mark all versions under the same parent_message_id as non-current
        self.db.query(Message).filter(
            Message.conversation_id == original_msg.conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        ).update({"is_current": False})
        self.db.commit()

        # 5. Extract the file references from the parent message
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                files = []
                for f in meta_files:
                    try:
                        file_input = deserialize_file_reference(f)
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")

        # 6. Load the context (up to the parent message)
        conversation_id = original_msg.conversation_id
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_id,
            before_time=parent_msg.created_at,
            max_history=current_settings().agent_max_history
        )

        # 7. Call agent_chat (version arguments passed; agent_chat persists)
        result = await self.agent_chat(
            message=user_message_content,
            conversation_id=conversation_id,
            config=config,
            files=files,
            user_id=user_id,
            variables=variables,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            workspace_id=str(workspace_id),
            history=filtered_history,
            skip_save=True,
            parent_message_id=parent_msg_id,
            version=new_version,
        )

        logger.info(
            "重新生成回复成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": result["message_id"],
                "version": new_version,
            }
        )

        return {
            "message_id": result["message_id"],
            "message": result["message"],
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
            message_id: uuid.UUID,
            config,
            workspace_id: uuid.UUID,
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Regenerate a reply (streaming, multi-version).

        Args:
            message_id: the original assistant message id
            config: AgentConfig
            workspace_id: workspace id
            user_id: user id
            variables: variables
            web_search: web search switch
            memory: long-term memory switch
            storage_type: storage type
            user_rag_memory_id: RAG memory id

        Yields:
            str: SSE-format event data
        """

        # 1. Load the original message
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. Locate the parent user message (trace by created_at when the
        # backfilled parent_message_id is missing; a soft-deleted parent is
        # auto-restored — see _locate_or_restore_parent_user_message)
        parent_msg = self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3. Query all versions under the same parent_message_id for the max
        # version
        from sqlalchemy import func
        max_version_result = self.db.scalars(
            select(func.max(Message.version))
            .where(
                Message.conversation_id == original_msg.conversation_id,
                Message.parent_message_id == parent_msg_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            )
        ).first()
        max_version = max_version_result or 0
        new_version = max_version + 1

        # 4. Mark all versions under the same parent_message_id as non-current
        self.db.query(Message).filter(
            Message.conversation_id == original_msg.conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        ).update({"is_current": False})
        self.db.commit()

        # 5. Extract the file references from the parent message
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                files = []
                for f in meta_files:
                    try:
                        file_input = deserialize_file_reference(f)
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")

        # 6. Load the context
        conversation_id = original_msg.conversation_id
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_id,
            before_time=parent_msg.created_at,
            max_history=current_settings().agent_max_history
        )

        # 7. Streaming call (version arguments passed; agent_chat_stream
        # persists)
        async for event_str in self.agent_chat_stream(
                message=user_message_content,
                conversation_id=conversation_id,
                config=config,
                files=files,
                user_id=user_id,
                variables=variables,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                workspace_id=str(workspace_id),
                history=filtered_history,
                skip_save=True,
                parent_message_id=parent_msg_id,
                version=new_version,
        ):
            yield event_str

        logger.info(
            "重新生成回复成功（流式）",
            extra={
                "original_message_id": str(message_id),
                "version": new_version,
            }
        )

    async def _load_history_before_message(
            self,
            conversation_id: uuid.UUID,
            before_time,
            max_history: int = 10
    ) -> List[Dict[str, Any]]:
        """Load the history messages before the given time (regeneration case)."""
        history_msgs = self.db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.is_current.is_not(False),
                Message.created_at < before_time,
                Message.is_deleted.is_not(True),
            )
            .order_by(Message.created_at.asc())
            .limit(max_history)
        ).all()

        filtered_history = []
        for msg in history_msgs:
            msg_dict = {
                "role": msg.role,
                "content": [{"type": "text", "text": msg.content}]
            }
            if msg.role == "user" and msg.meta_data:
                history_files = msg.meta_data.get("history_files", {})
                if history_files and history_files.get("content"):
                    msg_dict["content"].extend(history_files.get("content"))
            filtered_history.append(msg_dict)

        logger.debug(
            "加载指定时间前的历史消息",
            extra={"conversation_id": str(conversation_id), "loaded_count": len(filtered_history)}
        )

        return filtered_history


# ==================== Dependency injection functions ====================

def get_app_chat_service(
        db: Annotated[Session, Depends(get_db)]
) -> AppChatService:
    """Get the app chat service (dependency injection)."""
    return AppChatService(db)
