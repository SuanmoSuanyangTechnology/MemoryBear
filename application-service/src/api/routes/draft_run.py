"""Draft-run / workflow-debug routes.

Mirrors the monolith ``app_controller`` trial-run and node-debug surface:
agent / multi-agent / workflow draft runs, multi-model compare, single-node
run, node cache maintenance, debug state and rerun-from-last-debug.

Runtime plane: per-request async DB contexts, in-process execution only (no
sandbox runner). SSE event framing matches the monolith; each stream runs
through the shared keep-alive wrapper (heartbeat + idle cutoff).
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...db import get_async_db_context
from ...errors import BizCode, BusinessException, ResourceNotFoundException
from ...guards.workspace_access import require_workspace_access
from ...models import (
    AgentConfig,
    App,
    AppRelease,
    AppShare,
    AppType,
    Conversation,
    EndUser,
    EndUserInfo,
    HitLogSource,
    ModelConfig,
    ModelProvider,
    WorkflowConfig,
)
from ...repositories import knowledge_repository
from ...repositories.end_user_repository import EndUserRepository
from ...schemas import app_schema
from ...schemas.multi_agent_schema import MultiAgentRunRequest
from ...services.app_service import AppService
from ...services.draft_run_service import AgentRunService
from ...services.multi_agent_service import MultiAgentService
from ...services.workflow_service import WorkflowService
from ...services.workspace_service import get_workspace_storage_type_without_auth_async
from ...utils.datetime_utils import utcnow_naive
from ...utils.sse_stream import sse_response
from ...utils.workflow_validator import validate_workflow_config
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import fail, success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/apps", tags=["Draft Run"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]


# ==================== Runtime-config helpers ====================


@dataclass(frozen=True)
class _DraftRunAppSnapshot:
    id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    current_release_id: uuid.UUID | None


def _build_agent_config_snapshot(source):
    """Detached copy of a live AgentConfig row (deep-copied JSON-ish columns)."""
    return _build_agent_config(
        AgentConfig,
        id=source.id,
        app_id=source.app_id,
        system_prompt=source.system_prompt,
        default_model_config_id=source.default_model_config_id,
        model_parameters=copy.deepcopy(source.model_parameters),
        knowledge_retrieval=copy.deepcopy(source.knowledge_retrieval),
        memory=copy.deepcopy(source.memory),
        variables=copy.deepcopy(source.variables),
        tools=copy.deepcopy(source.tools),
        skills=copy.deepcopy(source.skills),
        features=copy.deepcopy(source.features),
        agent_role=source.agent_role,
        agent_domain=source.agent_domain,
        parent_agent_id=source.parent_agent_id,
        capabilities=copy.deepcopy(source.capabilities),
        is_active=source.is_active,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _build_agent_config(agent_config_cls, **kwargs):
    return agent_config_cls(**kwargs)


def _build_agent_config_from_release(*, release, real_config_id, created_at):
    """Rebuild a transient AgentConfig from a release snapshot config."""
    cfg = release.config or {}
    return _build_agent_config(
        AgentConfig,
        id=real_config_id or uuid.uuid4(),
        app_id=release.app_id,
        system_prompt=cfg.get("system_prompt", ""),
        default_model_config_id=release.default_model_config_id,
        model_parameters=copy.deepcopy(cfg.get("model_parameters")),
        knowledge_retrieval=copy.deepcopy(cfg.get("knowledge_retrieval")),
        memory=copy.deepcopy(cfg.get("memory", {})),
        variables=copy.deepcopy(cfg.get("variables", [])),
        tools=copy.deepcopy(cfg.get("tools", [])),
        skills=copy.deepcopy(cfg.get("skills", {})),
        features=copy.deepcopy(cfg.get("features", {})),
        is_active=True,
        created_at=created_at,
        updated_at=created_at,
    )


def _build_model_config_snapshot(source):
    """Detached copy of a live ModelConfig row (same fields as the host)."""
    snapshot = ModelConfig(
        id=source.id,
        model_id=source.model_id,
        tenant_id=source.tenant_id,
        logo=source.logo,
        name=source.name,
        provider=source.provider,
        type=source.type,
        is_composite=source.provider == ModelProvider.COMPOSITE,
        description=source.description,
        input_modalities=copy.deepcopy(getattr(source, "input_modalities", None) or []),
        output_modalities=copy.deepcopy(getattr(source, "output_modalities", None) or []),
        features=copy.deepcopy(getattr(source, "features", None) or []),
        config=copy.deepcopy(source.config),
        is_public=source.is_public,
        load_balance_strategy=source.load_balance_strategy,
        created_at=source.created_at,
        updated_at=source.updated_at,
        is_active=source.is_active,
    )
    return snapshot


async def _load_draft_run_app_snapshot(
        app_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
) -> _DraftRunAppSnapshot:
    """Read the minimal app state for a draft run (own workspace or shared)."""
    async with get_async_db_context() as db:
        app = await db.scalar(
            select(App).where(App.id == app_id).limit(1)
        )
        if not app:
            raise BusinessException("应用不存在", BizCode.NOT_FOUND)

        if workspace_id is not None and app.workspace_id != workspace_id:
            share_exists = await db.scalar(
                select(AppShare.id).where(
                    AppShare.source_app_id == app.id,
                    AppShare.target_workspace_id == workspace_id,
                    AppShare.is_active.is_(True),
                ).limit(1)
            )
            if not share_exists:
                raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)

        return _DraftRunAppSnapshot(
            id=app.id,
            workspace_id=app.workspace_id,
            type=app.type,
            current_release_id=app.current_release_id,
        )


async def _get_or_create_draft_end_user_id(
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        other_id: str,
) -> str:
    """Resolve the caller's end-user row for this app, creating it if needed.

    The first read is lock-free (the common case); the insert path takes a
    transaction-scoped advisory lock keyed by workspace+other_id and re-reads
    to stay race-free.
    """
    async with get_async_db_context() as db:
        user_repo = EndUserRepository(db)

        existing_end_user = await user_repo.get_end_user_by_other_id_async(
            workspace_id, other_id
        )
        if existing_end_user and existing_end_user.app_id == app_id:
            return str(existing_end_user.id)

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{workspace_id}|{other_id}"},
        )
        end_user = await user_repo.get_end_user_by_other_id_async(
            workspace_id, other_id
        )
        if end_user:
            end_user.app_id = app_id
            await db.commit()
            return str(end_user.id)

        end_user = EndUser(
            app_id=app_id,
            workspace_id=workspace_id,
            other_id=other_id,
        )
        db.add(end_user)
        await db.flush()
        db.add(
            EndUserInfo(
                end_user_id=end_user.id,
                other_name="",
                aliases=[],
                meta_data={},
            )
        )
        await db.commit()
        return str(end_user.id)


async def _ensure_workflow_conversation_ready(
        *,
        conversation_id: str | None,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
) -> str | None:
    """Validate an incoming conversation id; the app's own or a shared app's
    conversation is accepted, anything else is rejected."""
    if not conversation_id:
        return conversation_id

    try:
        conv_uuid = uuid.UUID(conversation_id)
    except Exception as exc:
        raise BusinessException(
            f"会话不存在: {conversation_id}",
            BizCode.NOT_FOUND,
            cause=exc,
        ) from exc

    async with get_async_db_context() as db:
        conversation = await db.get(Conversation, conv_uuid)
        if not conversation:
            raise BusinessException(f"会话不存在: {conversation_id}", BizCode.NOT_FOUND)

        if conversation.workspace_id != workspace_id:
            share_exists = await db.scalar(
                select(AppShare.id).where(
                    AppShare.source_app_id == app_id,
                    AppShare.target_workspace_id == workspace_id,
                    AppShare.is_active.is_(True),
                ).limit(1)
            )
            same_app = conversation.app_id == app_id
            if not share_exists and not same_app:
                raise BusinessException("会话不属于当前工作空间", BizCode.PERMISSION_DENIED)

    return conversation_id


async def _load_workflow_runtime_config(
        *,
        app_id: uuid.UUID,
        app_workspace_id: uuid.UUID,
        current_release_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
) -> tuple[object, bool]:
    """Load the workflow config to run: release snapshot for shared apps,
    validated draft config otherwise; returns (config, is_shared)."""
    is_shared = app_workspace_id != workspace_id
    workflow_service = WorkflowService()

    if is_shared:
        if not current_release_id:
            raise BusinessException("该应用尚未发布，无法使用", BizCode.AGENT_CONFIG_MISSING)

        async with get_async_db_context() as db:
            release = await db.get(AppRelease, current_release_id)
            if not release:
                raise BusinessException("发布版本不存在", BizCode.AGENT_CONFIG_MISSING)
            real_config_id = await db.scalar(
                select(WorkflowConfig.id).where(
                    WorkflowConfig.app_id == release.app_id,
                    WorkflowConfig.is_active.is_(True),
                ).limit(1)
            )
            cfg = release.config or {}
            now = release.created_at or utcnow_naive()

        return WorkflowConfig(
            id=real_config_id or uuid.uuid4(),
            app_id=app_id,
            nodes=cfg.get("nodes", []),
            edges=cfg.get("edges", []),
            variables=cfg.get("variables", []),
            environment_variables=cfg.get("environment_variables") or [],
            execution_config=cfg.get("execution_config", {}),
            triggers=cfg.get("triggers", []),
            features=cfg.get("features", {}),
            # Normalization lives on AppService (the host referenced it on
            # WorkflowService, which has no such method and would raise).
            workflow_type=AppService(None)._normalize_workflow_type(cfg.get("workflow_type")),
            is_active=True,
            created_at=now,
            updated_at=now,
        ), True

    config = await workflow_service.get_workflow_config_snapshot_async(app_id)
    if not config:
        raise BusinessException("工作流配置不存在，无法运行", BizCode.CONFIG_MISSING)

    config_id = config.id
    config_app_id = config.app_id
    config_is_active = config.is_active
    config_created_at = config.created_at
    config_updated_at = config.updated_at
    config_dict = workflow_service._prepare_workflow_config_dict(
        nodes=config.nodes,
        edges=config.edges,
        variables=config.variables,
        environment_variables=config.environment_variables,
        execution_config=config.execution_config,
        features=config.features,
        triggers=config.triggers,
        workflow_type=config.workflow_type,
    )

    is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
    if not is_valid:
        raise BusinessException(
            code=BizCode.INVALID_PARAMETER,
            message=f"工作流配置无效: {'; '.join(errors)}",
        )

    return WorkflowConfig(
        id=config_id,
        app_id=config_app_id,
        nodes=config_dict["nodes"],
        edges=config_dict["edges"],
        variables=config_dict["variables"],
        environment_variables=config_dict["environment_variables"],
        execution_config=config_dict["execution_config"],
        features=config_dict["features"],
        triggers=config_dict["triggers"],
        workflow_type=config_dict["workflow_type"],
        is_active=config_is_active,
        created_at=config_created_at,
        updated_at=config_updated_at,
    ), False


async def _prepare_draft_memory_context_async(
        *,
        workspace_id: uuid.UUID,
) -> tuple[str, str]:
    """Resolve the memory storage type and the user RAG memory knowledge id.

    Workspace access is already enforced by the WorkspaceId dependency, so the
    no-auth storage-type lookup is sufficient here.
    """
    async with get_async_db_context() as db:
        storage_type = await get_workspace_storage_type_without_auth_async(
            db=db,
            workspace_id=workspace_id,
        )
        if storage_type is None:
            storage_type = "neo4j"

        user_rag_memory_id = ""
        knowledge = await knowledge_repository.get_knowledge_by_name_async(
            db=db,
            name="USER_RAG_MERORY",
            workspace_id=workspace_id,
        )
        if knowledge:
            user_rag_memory_id = str(knowledge.id)

    return storage_type, user_rag_memory_id


async def _load_agent_runtime_config(
        *,
        app_id: uuid.UUID,
        app_workspace_id: uuid.UUID,
        current_release_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
):
    """Load (agent config, model config, is_shared) for an agent draft run."""
    is_shared = app_workspace_id != workspace_id

    async with get_async_db_context() as db:
        if is_shared:
            if not current_release_id:
                raise BusinessException("该应用尚未发布，无法使用", BizCode.AGENT_CONFIG_MISSING)

            release = await db.get(AppRelease, current_release_id)
            if not release:
                raise BusinessException("发布版本不存在", BizCode.AGENT_CONFIG_MISSING)

            real_config = await db.scalar(
                select(AgentConfig).where(AgentConfig.app_id == release.app_id).limit(1)
            )
            now = release.created_at or utcnow_naive()
            agent_cfg_snapshot = _build_agent_config_from_release(
                release=release,
                real_config_id=real_config.id if real_config else None,
                created_at=now,
            )
        else:
            agent_cfg = await db.scalar(
                select(AgentConfig).where(AgentConfig.app_id == app_id).limit(1)
            )
            if not agent_cfg:
                raise BusinessException("Agent 配置不存在", BizCode.AGENT_CONFIG_MISSING)
            agent_cfg_snapshot = _build_agent_config_snapshot(agent_cfg)

        model_config = None
        if agent_cfg_snapshot.default_model_config_id:
            model_config = await db.scalar(
                select(ModelConfig)
                .where(ModelConfig.id == agent_cfg_snapshot.default_model_config_id)
            )
            if not model_config:
                raise ResourceNotFoundException(
                    "模型配置", str(agent_cfg_snapshot.default_model_config_id)
                )

        if not model_config:
            raise BusinessException("模型配置不存在，无法试运行", BizCode.AGENT_CONFIG_MISSING)

        model_config_snapshot = _build_model_config_snapshot(model_config)

    return agent_cfg_snapshot, model_config_snapshot, is_shared


# ==================== Draft run ====================


@router.post("/{app_id}/draft/run", summary="试运行 Agent（使用当前草稿配置）")
async def draft_run(
        app_id: UUID,
        payload: app_schema.DraftRunRequest,
        workspace_id: WorkspaceId,
        principal: Caller,
):
    """Run the app's current draft config (no publish required).

    - Uses the current AgentConfig / WorkflowConfig / multi-agent config
    - Supports streaming and non-streaming responses
    """
    current_user_id = str(principal.actor_id)
    storage_type: str | None = None
    user_rag_memory_id = ""

    app = await _load_draft_run_app_snapshot(app_id, workspace_id)
    app_type = AppType(app.type)
    supported_types = (AppType.AGENT, AppType.MULTI_AGENT, AppType.WORKFLOW, AppType.PURE_WORKFLOW)
    if app_type not in supported_types:
        raise BusinessException(
            "只有 Agent , Workflow 类型应用支持试运行", BizCode.APP_TYPE_NOT_SUPPORTED
        )

    if app_type != AppType.PURE_WORKFLOW and not payload.message:
        raise BusinessException("当前应用类型要求必须传入 message", BizCode.INVALID_PARAMETER)

    if payload.user_id is None:
        payload.user_id = await _get_or_create_draft_end_user_id(
            app_id=app_id,
            workspace_id=app.workspace_id,
            other_id=current_user_id,
        )

    is_agent_new_conversation = app_type == AppType.AGENT and not payload.conversation_id
    should_prepare_conversation = (
        not is_agent_new_conversation
        and (payload.conversation_id or app_type not in (AppType.WORKFLOW, AppType.PURE_WORKFLOW))
    )

    if app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
        if should_prepare_conversation:
            payload.conversation_id = await _ensure_workflow_conversation_ready(
                conversation_id=payload.conversation_id,
                app_id=app_id,
                workspace_id=workspace_id,
            )

        config, is_shared = await _load_workflow_runtime_config(
            app_id=app_id,
            app_workspace_id=app.workspace_id,
            current_release_id=app.current_release_id,
            workspace_id=workspace_id,
        )
        if payload.stream:
            logger.debug(
                "开始工作流流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message or ""),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE

            async def event_generator():
                _wf_service = WorkflowService()
                async for event in _wf_service.run_stream(
                        app_id=app_id,
                        payload=payload,
                        config=config,
                        workspace_id=workspace_id,
                        source=source,
                        trigger_payload=payload.trigger_payload
                ):
                    event_type = event.get("event", "message")
                    event_data = event.get("data", {})
                    sse_message = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                    yield sse_message

            return sse_response(event_generator())

        async with get_async_db_context() as db:
            workflow_service = WorkflowService(db)
            logger.debug(
                "开始非流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message or ""),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE
            result = await workflow_service.run(
                app_id,
                payload,
                config,
                workspace_id,
                source=source,
                trigger_payload=payload.trigger_payload,
            )

            logger.debug(
                "工作流试运行返回结果",
                extra={
                    "result_type": str(type(result)),
                    "has_response": "response" in result if isinstance(result, dict) else False
                }
            )
            return success(
                data=result,
                msg="工作流任务执行成功"
            )
    if app_type == AppType.AGENT:
        storage_type, user_rag_memory_id = await _prepare_draft_memory_context_async(
            workspace_id=workspace_id,
        )

        async with get_async_db_context() as db:
            draft_service = AgentRunService(db)
            if should_prepare_conversation:
                conversation_id = await draft_service._ensure_conversation(
                    conversation_id=payload.conversation_id,
                    app_id=app_id,
                    workspace_id=workspace_id,
                    user_id=payload.user_id
                )
                payload.conversation_id = conversation_id

        agent_cfg, model_config, is_shared = await _load_agent_runtime_config(
            app_id=app_id,
            app_workspace_id=app.workspace_id,
            current_release_id=app.current_release_id,
            workspace_id=workspace_id,
        )

        if payload.stream:
            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE

            async def event_generator():
                async with get_async_db_context() as stream_db:
                    _draft_service = AgentRunService(stream_db)
                    async for event in _draft_service.run_stream(
                            agent_config=agent_cfg,
                            model_config=model_config,
                            message=payload.message,
                            workspace_id=workspace_id,
                            conversation_id=payload.conversation_id,
                            user_id=payload.user_id or current_user_id,
                            variables=payload.variables,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            files=payload.files,
                            source=source,
                    ):
                        yield event

            return sse_response(event_generator())

        logger.debug(
            "开始非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message or ""),
                "has_conversation_id": bool(payload.conversation_id),
                "has_variables": bool(payload.variables),
                "has_files": bool(payload.files)
            }
        )

        async with get_async_db_context() as db:
            draft_service = AgentRunService(db)
            source = HitLogSource.EXTERNAL if is_shared else HitLogSource.CONSOLE
            result = await draft_service.run(
                agent_config=agent_cfg,
                model_config=model_config,
                message=payload.message,
                workspace_id=workspace_id,
                conversation_id=payload.conversation_id,
                user_id=payload.user_id or current_user_id,
                variables=payload.variables,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                files=payload.files,
                source=source
            )

        logger.debug(
            "试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "result_keys": list(result.keys()) if isinstance(result, dict) else "not_dict"
            }
        )

        try:
            validated_result = app_schema.DraftRunResponse.model_validate(result)
            logger.debug("结果验证成功")
            return success(data=validated_result)
        except Exception as e:
            logger.error(
                "结果验证失败",
                extra={
                    "error": str(e),
                    "error_type": str(type(e)),
                    "result": str(result)[:200]
                }
            )
            raise

    storage_type, user_rag_memory_id = await _prepare_draft_memory_context_async(
        workspace_id=workspace_id,
    )

    async with get_async_db_context() as db:
        draft_service = AgentRunService(db)
        if should_prepare_conversation:
            conversation_id = await draft_service._ensure_conversation(
                conversation_id=payload.conversation_id,
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=payload.user_id
            )
            payload.conversation_id = conversation_id

    if app_type == AppType.MULTI_AGENT:
        multi_agent_request = MultiAgentRunRequest(
            message=payload.message,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id or current_user_id,
            variables=payload.variables or {},
            use_llm_routing=True,
            # Deep thinking: same as the Agent trial run — decided solely by the
            # cluster's model_parameters.deep_thinking; the request no longer
            # needs to pass thinking (is_deep_thinking_on requires both true,
            # so this is always True here)
            thinking=True,
            # Web search: default False = legacy behavior; also requires the
            # cluster's features.web_search.enabled (supervisor only).
            web_search=bool(getattr(payload, "web_search", False)),
            # Attachments: default empty = legacy behavior; gated by the
            # cluster's features.file_upload.
            files=list(getattr(payload, "files", None) or []),
        )

        if payload.stream:
            logger.debug(
                "开始多智能体流式试运行",
                extra={
                    "app_id": str(app_id),
                    "message_length": len(payload.message or ""),
                    "has_conversation_id": bool(payload.conversation_id)
                }
            )

            async def event_generator():
                async with get_async_db_context() as stream_db:
                    multiservice = MultiAgentService(stream_db)
                    async for event in multiservice.run_stream(
                            app_id=app_id,
                            request=multi_agent_request,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id
                    ):
                        yield event

            return sse_response(event_generator())

        logger.debug(
            "开始多智能体非流式试运行",
            extra={
                "app_id": str(app_id),
                "message_length": len(payload.message or ""),
                "has_conversation_id": bool(payload.conversation_id)
            }
        )

        async with get_async_db_context() as db:
            multiservice = MultiAgentService(db)
            result = await multiservice.run(app_id, multi_agent_request)

        logger.debug(
            "多智能体试运行返回结果",
            extra={
                "result_type": str(type(result)),
                "has_response": "response" in result if isinstance(result, dict) else False
            }
        )

        return success(
            data=result,
            msg="多 Agent 任务执行成功"
        )

    return fail(
        msg="未知应用类型",
        code=422
    )


@router.post("/{app_id}/draft/run/compare", summary="多模型对比试运行")
async def draft_run_compare(
        app_id: UUID,
        payload: app_schema.DraftRunCompareRequest,
        workspace_id: WorkspaceId,
        principal: Caller,
):
    """Run the same draft message against 1-5 model configs and compare.

    - Different models or the same model with different parameter overrides
    - Parallel or sequential execution (non-streaming); streaming runs serially
    - Returns per-model results plus performance comparison
    """
    app = await _load_draft_run_app_snapshot(app_id, workspace_id)
    if AppType(app.type) != AppType.AGENT:
        raise BusinessException("只有 Agent 类型应用支持对比试运行", BizCode.APP_TYPE_NOT_SUPPORTED)

    storage_type, user_rag_memory_id = await _prepare_draft_memory_context_async(
        workspace_id=workspace_id,
    )

    logger.info(
        "多模型对比试运行",
        extra={
            "app_id": str(app_id),
            "model_count": len(payload.models),
            "parallel": payload.parallel,
            "stream": payload.stream
        }
    )

    if payload.user_id is None:
        payload.user_id = await _get_or_create_draft_end_user_id(
            app_id=app_id,
            workspace_id=app.workspace_id,
            other_id=str(principal.actor_id),
        )

    agent_cfg, _, _ = await _load_agent_runtime_config(
        app_id=app_id,
        app_workspace_id=app.workspace_id,
        current_release_id=app.current_release_id,
        workspace_id=workspace_id,
    )

    async with get_async_db_context() as db:
        # Batch-fetch every requested model config in one query (no per-item
        # query inside the loop); missing ids still fail with not-found.
        model_ids = [model_item.model_config_id for model_item in payload.models]
        rows = await db.execute(select(ModelConfig).where(ModelConfig.id.in_(model_ids)))
        configs_by_id = {row.id: row for row in rows.scalars().all()}

        model_configs = []
        for model_item in payload.models:
            model_config = configs_by_id.get(model_item.model_config_id)
            if not model_config:
                raise ResourceNotFoundException("模型配置", str(model_item.model_config_id))

            agent_model_params = agent_cfg.model_parameters
            if hasattr(agent_model_params, 'model_dump'):
                agent_model_params = agent_model_params.model_dump()
            elif not isinstance(agent_model_params, dict):
                agent_model_params = {}

            item_model_params = model_item.model_parameters
            if hasattr(item_model_params, 'model_dump'):
                item_model_params = item_model_params.model_dump()
            elif not isinstance(item_model_params, dict):
                item_model_params = {}

            merged_parameters = {
                **(agent_model_params or {}),
                **(item_model_params or {})
            }

            model_configs.append({
                "model_config": _build_model_config_snapshot(model_config),
                "parameters": merged_parameters,
                "label": model_item.label or model_config.name,
                "model_config_id": model_item.model_config_id,
                "conversation_id": model_item.conversation_id
            })

    # Feature flags come from the agent config features (same as draft_run)
    features_config: dict = agent_cfg.features or {}
    if hasattr(features_config, 'model_dump'):
        features_config = features_config.model_dump()
    web_search_feature = features_config.get("web_search", {})
    web_search = isinstance(web_search_feature, dict) and web_search_feature.get("enabled", False)

    if payload.stream:
        source = HitLogSource.CONSOLE

        async def event_generator():
            async with get_async_db_context() as db:
                draft_service = AgentRunService(db)
                async for event in draft_service.run_compare_stream(
                        agent_config=agent_cfg,
                        models=model_configs,
                        message=payload.message,
                        workspace_id=workspace_id,
                        conversation_id=payload.conversation_id,
                        user_id=payload.user_id,
                        variables=payload.variables,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        web_search=web_search,
                        memory=True,
                        parallel=payload.parallel,
                        timeout=payload.timeout or 60,
                        files=payload.files,
                        source=source,
                ):
                    yield event

        return sse_response(event_generator())

    # Non-streaming branch
    source = HitLogSource.CONSOLE
    async with get_async_db_context() as db:
        draft_service = AgentRunService(db)
        result = await draft_service.run_compare(
            agent_config=agent_cfg,
            models=model_configs,
            message=payload.message,
            workspace_id=workspace_id,
            conversation_id=payload.conversation_id,
            user_id=payload.user_id,
            variables=payload.variables,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            web_search=web_search,
            memory=True,
            parallel=payload.parallel,
            timeout=payload.timeout or 60,
            files=payload.files,
            source=source,
        )

    logger.info(
        "多模型对比完成",
        extra={
            "app_id": str(app_id),
            "successful": result["successful_count"],
            "failed": result["failed_count"]
        }
    )

    return success(data=app_schema.DraftRunCompareResponse(**result))


# ==================== Workflow node debug ====================


@router.post("/{app_id}/workflow/nodes/{node_id}/run", summary="单节点试运行")
async def run_single_workflow_node(
        app_id: UUID,
        node_id: str,
        payload: app_schema.NodeRunRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
        principal: Caller,
):
    """Run a single workflow node in isolation.

    ``inputs`` supports these key formats:
    - Node variables: "node_id.var_name"
    - System variables: "sys.message", "sys.files"
    """
    workflow_service = WorkflowService(db)
    config = workflow_service.get_workflow_config(app_id)
    if not config:
        raise BusinessException("工作流配置不存在，无法运行", BizCode.CONFIG_MISSING)

    raw_inputs = payload.inputs or {}
    input_data = {
        "message": raw_inputs.pop("sys.message", None),
        "files": raw_inputs.pop("sys.files", None),
        "user_id": raw_inputs.pop("sys.user_id", str(principal.actor_id)),
        "trigger_payload": raw_inputs.pop("trigger_payload", None),
        "inputs": raw_inputs,
        "conversation_id": "",
        "conv_messages": [],
    }

    if payload.stream:
        async def event_generator():
            async for event in workflow_service.run_single_node_stream(
                    app_id=app_id,
                    node_id=node_id,
                    config=config,
                    workspace_id=workspace_id,
                    input_data=input_data,
            ):
                yield (
                    f"event: {event['event']}\ndata: "
                    f"{json.dumps(event['data'], ensure_ascii=False)}\n\n"
                )

        return sse_response(event_generator())

    result = await workflow_service.run_single_node(
        app_id=app_id,
        node_id=node_id,
        config=config,
        workspace_id=workspace_id,
        input_data=input_data,
    )
    return success(data=result)


@router.get(
    "/{app_id}/workflow/nodes/{node_id}/last_run",
    summary="获取节点上次运行",
    response_model=ApiResponse,
)
def get_workflow_node_last_run(
        app_id: UUID,
        node_id: str,
        workspace_id: WorkspaceId,
        db: DbSession,
        source: str | None = Query(None, description="workflow_execution 或 single_node_debug"),
):
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)

    if source not in (None, "workflow_execution", "single_node_debug"):
        raise BusinessException("source 参数无效", BizCode.INVALID_PARAMETER)

    result = WorkflowService(db).get_node_last_run(app_id=app_id, node_id=node_id, source=source)
    return success(data=result)


@router.put(
    "/{app_id}/workflow/nodes/{node_id}/cache",
    summary="更新节点缓存",
    response_model=ApiResponse,
)
def update_workflow_node_cache(
        app_id: UUID,
        node_id: str,
        payload: app_schema.NodeCacheUpdateRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    result = WorkflowService(db).update_node_cache(
        app_id=app_id,
        node_id=node_id,
        result_data=payload.result_data,
        patches=[item.model_dump() for item in payload.patches],
    )
    if not result:
        raise BusinessException("节点缓存不存在", BizCode.NOT_FOUND)
    return success(data=result)


@router.delete(
    "/{app_id}/workflow/nodes/{node_id}/cache",
    summary="失效节点缓存",
    response_model=ApiResponse,
)
def invalidate_workflow_node_cache(
        app_id: UUID,
        node_id: str,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    affected = WorkflowService(db).invalidate_node_cache(app_id=app_id, node_id=node_id)
    if affected <= 0:
        raise BusinessException("节点缓存不存在", BizCode.NOT_FOUND)
    return success(data={"affected": affected})


@router.post(
    "/{app_id}/workflow/cache/reset",
    summary="清空工作流调试缓存",
    response_model=ApiResponse,
)
def reset_workflow_debug_cache(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    result = WorkflowService(db).reset_workflow_debug_cache(app_id=app_id)
    return success(data=result)


@router.get(
    "/{app_id}/workflow/debug/state",
    summary="获取工作流当前调试状态",
    response_model=ApiResponse,
)
def get_workflow_debug_state(
        app_id: UUID,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    result = WorkflowService(db).get_workflow_debug_state(app_id=app_id)
    return success(data=result)


@router.post(
    "/{app_id}/workflow/nodes/{node_id}/rerun",
    summary="基于最近一次单节点调试输入重跑",
    response_model=ApiResponse,
)
async def rerun_workflow_node(
        app_id: UUID,
        node_id: str,
        payload: app_schema.NodeRerunRequest,
        workspace_id: WorkspaceId,
        db: DbSession,
):
    service = AppService(db)
    app = service._get_app_or_404(app_id)
    service._validate_app_accessible(app, workspace_id)
    workflow_service = WorkflowService(db)
    config = workflow_service.get_workflow_config(app_id)
    if not config:
        raise BusinessException("工作流配置不存在，无法运行", BizCode.CONFIG_MISSING)
    result = await workflow_service.rerun_node_from_last_debug(
        app_id=app_id,
        node_id=node_id,
        config=config,
        workspace_id=workspace_id,
        invalidate_cache=payload.invalidate_cache,
        bypass_cache=payload.bypass_cache,
    )
    return success(data=result)


__all__ = ["router"]
