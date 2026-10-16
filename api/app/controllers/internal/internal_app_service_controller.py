"""应用服务内部回调控制器

供拆分出的应用服务（application-service）在共存期回调老单体执行记忆读写与
上下文引擎操作，避免应用服务本地复刻这些逻辑（状态在单体侧单写）。

认证方式: API Key（memory scope）。记忆类端点同时要求显式 X-Workspace-Id 头：
/v1 面工作空间取自 Key 绑定，预置服务 Key 需服务多工作空间，因此由调用方显式
指定并按端点校验 end_user 归属。

路由前缀: /internal/app-service
最终路径: /api/internal/app-service/...

端点:
    POST /context-engine/prepare-app-input              对话主流程上下文准备
    POST /context-engine/after-app-turn                 对话轮次后摘要状态推进
    POST /context-engine/prepare-workflow-history-prefix 工作流历史前缀准备
    POST /context-engine/after-workflow-turn            工作流轮次后摘要状态推进
    POST /memory/read                                   长期记忆检索
    POST /memory/ingest-workflow                        工作流消息摄入（workflow 语义）
    POST /memory/ingest-agent                           Agent 回合消息摄入（user+assistant 成对）
    GET  /memory/workspace-config                       工作空间启用中的记忆配置
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import validate_end_user_in_workspace_async
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.memory.enums import Neo4jNodeType, SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.response_utils import success
from app.db import get_async_db_context
from app.schemas.api_key_schema import ApiKeyAuth
from app.services.context_engine_manager import ContextEngineManager
from app.services.memory_config_service import MemoryConfigService

router = APIRouter(prefix="/internal/app-service", tags=["internal-app-service"])
logger = get_business_logger()


def _parse_workspace_id(raw: Optional[str]) -> uuid.UUID:
    """解析显式 X-Workspace-Id 头；记忆类端点的唯一工作空间来源。"""
    if not raw:
        raise BusinessException(
            "X-Workspace-Id header is required", BizCode.INVALID_PARAMETER
        )
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError) as exc:
        raise BusinessException(
            f"Invalid X-Workspace-Id header: {raw}", BizCode.INVALID_PARAMETER
        ) from exc


def _parse_uuid(raw: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError) as exc:
        raise BusinessException(
            f"Invalid {field_name}: {raw}", BizCode.INVALID_PARAMETER
        ) from exc


def _parse_iso_created_at(raw: Optional[str]) -> Optional[datetime]:
    """还原调用方序列化的 ISO 时间；写入列要求 datetime，字符串不容许透传。"""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BusinessException(
            f"Invalid created_at: {raw}", BizCode.INVALID_PARAMETER
        ) from exc


# ──────────────────────────────────────────────────────────────
# Request models
# ──────────────────────────────────────────────────────────────

class PrepareAppInputRequest(BaseModel):
    features: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str
    system_prompt: str
    current_input: str
    current_provider: Optional[str] = None
    legacy_max_history: int = 10
    scope_key: str = "conversation"
    model_config_id: Optional[str] = None
    include_cross_session: bool = True


class AfterAppTurnRequest(BaseModel):
    features: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str
    current_provider: Optional[str] = None
    legacy_max_history: int = 10
    scope_key: str = "conversation"
    model_config_id: Optional[str] = None


class WorkflowTurnRequest(BaseModel):
    features: dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = None
    scope_key: str
    workflow_messages: list[dict[str, Any]] = Field(default_factory=list)
    window_size: int = 20
    model_config_id: Optional[str] = None


class PrepareWorkflowHistoryPrefixRequest(WorkflowTurnRequest):
    current_input: str


class MemoryReadRequest(BaseModel):
    end_user_id: str
    query: str
    search_switch: str
    config_id: Optional[str] = None
    history: Optional[list] = None
    limit: int = Field(default=10, ge=1, le=100)
    includes: Optional[list[str]] = None
    skip_summary: bool = False
    enable_rerank: bool = False
    record_display: bool = False
    storage_type: str = "neo4j"
    user_rag_memory_id: str = ""
    conversation_id: Optional[str] = None
    language: str = "zh"
    draft: bool = False


class MemoryIngestWorkflowRequest(BaseModel):
    messages: list[dict[str, Any]]
    conversation_id: str
    end_user_id: str
    config_id: str
    language: str = "zh"
    storage_type: str = "neo4j"
    user_rag_memory_id: str = ""


class MemoryIngestAgentMessage(BaseModel):
    id: Optional[str] = None
    role: str
    content: str
    meta_data: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None
    should_memorize: bool = True


class MemoryIngestAgentRequest(BaseModel):
    messages: list[MemoryIngestAgentMessage]
    conversation_id: str
    app_id: str
    end_user_id: str = ""
    config_id: str = ""
    language: str = "zh"
    storage_type: str = "neo4j"
    user_rag_memory_id: str = ""


# ──────────────────────────────────────────────────────────────
# Context engine
# ──────────────────────────────────────────────────────────────

@router.post("/context-engine/prepare-app-input")
@require_api_key_self_db(scopes=["memory"])
async def context_engine_prepare_app_input(
        payload: PrepareAppInputRequest,
        api_key_auth: ApiKeyAuth = None,
):
    """对话主流程上下文准备；整体在单体侧执行，返回 (system_prompt, history) 或空。"""
    logger.info(
        f"Internal context engine prepare-app-input - key_workspace: {api_key_auth.workspace_id}, "
        f"conversation: {payload.conversation_id}"
    )
    async with get_async_db_context() as db:
        prepared = await ContextEngineManager(db).prepare_app_agent_input(
            features=payload.features,
            conversation_id=_parse_uuid(payload.conversation_id, "conversation_id"),
            system_prompt=payload.system_prompt,
            current_input=payload.current_input,
            current_provider=payload.current_provider,
            legacy_max_history=payload.legacy_max_history,
            scope_key=payload.scope_key,
            model_config_id=payload.model_config_id,
            include_cross_session=payload.include_cross_session,
        )
    if prepared is None:
        return success(data=None)
    system_prompt, history = prepared
    return success(data={"system_prompt": system_prompt, "history": history})


@router.post("/context-engine/after-app-turn")
@require_api_key_self_db(scopes=["memory"])
async def context_engine_after_app_turn(
        payload: AfterAppTurnRequest,
        api_key_auth: ApiKeyAuth = None,
):
    """对话轮次后推进摘要状态；返回是否触发摘要更新。"""
    logger.info(
        f"Internal context engine after-app-turn - key_workspace: {api_key_auth.workspace_id}, "
        f"conversation: {payload.conversation_id}"
    )
    async with get_async_db_context() as db:
        updated = await ContextEngineManager(db).after_app_turn(
            features=payload.features,
            conversation_id=_parse_uuid(payload.conversation_id, "conversation_id"),
            current_provider=payload.current_provider,
            legacy_max_history=payload.legacy_max_history,
            scope_key=payload.scope_key,
            model_config_id=payload.model_config_id,
        )
    return success(data={"updated": bool(updated)})


@router.post("/context-engine/prepare-workflow-history-prefix")
@require_api_key_self_db(scopes=["memory"])
async def context_engine_prepare_workflow_history_prefix(
        payload: PrepareWorkflowHistoryPrefixRequest,
        api_key_auth: ApiKeyAuth = None,
):
    """工作流节点历史前缀准备；返回消息列表或空。"""
    logger.info(
        f"Internal context engine prepare-workflow-history-prefix - key_workspace: "
        f"{api_key_auth.workspace_id}, conversation: {payload.conversation_id}, scope: {payload.scope_key}"
    )
    async with get_async_db_context() as db:
        prepared = await ContextEngineManager(db).prepare_workflow_history_prefix(
            features=payload.features,
            conversation_id=payload.conversation_id,
            scope_key=payload.scope_key,
            current_input=payload.current_input,
            workflow_messages=payload.workflow_messages,
            window_size=payload.window_size,
            model_config_id=payload.model_config_id,
        )
    if prepared is None:
        return success(data=None)
    return success(data={"history": prepared})


@router.post("/context-engine/after-workflow-turn")
@require_api_key_self_db(scopes=["memory"])
async def context_engine_after_workflow_turn(
        payload: WorkflowTurnRequest,
        api_key_auth: ApiKeyAuth = None,
):
    """工作流轮次后推进摘要状态；返回是否触发摘要更新。"""
    logger.info(
        f"Internal context engine after-workflow-turn - key_workspace: {api_key_auth.workspace_id}, "
        f"conversation: {payload.conversation_id}, scope: {payload.scope_key}"
    )
    async with get_async_db_context() as db:
        updated = await ContextEngineManager(db).after_workflow_turn(
            features=payload.features,
            conversation_id=payload.conversation_id,
            scope_key=payload.scope_key,
            workflow_messages=payload.workflow_messages,
            window_size=payload.window_size,
            model_config_id=payload.model_config_id,
        )
    return success(data={"updated": bool(updated)})


# ──────────────────────────────────────────────────────────────
# Memory
# ──────────────────────────────────────────────────────────────

@router.post("/memory/read")
@require_api_key_self_db(scopes=["memory"])
async def internal_memory_read(
        payload: MemoryReadRequest,
        x_workspace_id: Optional[str] = Header(None, alias="X-Workspace-Id"),
        api_key_auth: ApiKeyAuth = None,
):
    """长期记忆检索；工作空间为显式头，end_user 归属按该空间校验。"""
    workspace_id = _parse_workspace_id(x_workspace_id)
    logger.info(
        f"Internal memory read - key_workspace: {api_key_auth.workspace_id}, "
        f"workspace: {workspace_id}, end_user_id: {payload.end_user_id}"
    )
    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(db, payload.end_user_id, workspace_id)
        # 合并路由：改写为合并目标用户，避免取到旧用户上的记忆配置
        end_user_id = str(end_user.id)
        config_id = (
            _parse_uuid(payload.config_id, "config_id")
            if payload.config_id
            else await MemoryConfigService(db).get_config_id_by_end_user_async(end_user_id)
        )

    includes = [Neo4jNodeType(t) for t in payload.includes] if payload.includes else None
    service = await MemoryService.create(
        config_id,
        end_user_id=end_user_id,
        workspace_id=str(workspace_id),
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id or None,
        conversation_id=payload.conversation_id,
        language=payload.language,
        draft=payload.draft,
    )
    memory = await service.read(
        payload.query,
        search_switch=SearchStrategy(payload.search_switch),
        history=payload.history or [],
        limit=payload.limit,
        includes=includes,
        skip_summary=payload.skip_summary,
        enable_rerank=payload.enable_rerank,
        record_display=payload.record_display,
    )
    return success(data={
        "content": memory.content,
        "memories": [item.model_dump() for item in memory.memories],
    })


@router.post("/memory/ingest-workflow")
@require_api_key_self_db(scopes=["memory"])
async def internal_memory_ingest_workflow(
        payload: MemoryIngestWorkflowRequest,
        x_workspace_id: Optional[str] = Header(None, alias="X-Workspace-Id"),
        api_key_auth: ApiKeyAuth = None,
):
    """工作流消息摄入；保持 workflow 来源语义（与 /v1/write 的 service_api 区分）。"""
    workspace_id = _parse_workspace_id(x_workspace_id)
    logger.info(
        f"Internal memory ingest-workflow - key_workspace: {api_key_auth.workspace_id}, "
        f"workspace: {workspace_id}, end_user_id: {payload.end_user_id}"
    )
    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(db, payload.end_user_id, workspace_id)
        end_user_id = str(end_user.id)

    await MemoryService.ingest_workflow_messages(
        messages=payload.messages,
        conversation_id=payload.conversation_id,
        end_user_id=end_user_id,
        config_id=payload.config_id,
        workspace_id=str(workspace_id),
        language=payload.language,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )
    return success()


@router.post("/memory/ingest-agent")
@require_api_key_self_db(scopes=["memory"])
async def internal_memory_ingest_agent(
        payload: MemoryIngestAgentRequest,
        x_workspace_id: Optional[str] = Header(None, alias="X-Workspace-Id"),
        api_key_auth: ApiKeyAuth = None,
):
    """Agent 回合消息摄入；同一回合 user+assistant 一次派发，seq 连续分配。

    消息按宿主属性集（id/role/content/created_at/meta_data/should_memorize）
    重建后再入流水线，与单体内部 dispatch_memory_pair 的入参形态一致。
    """
    workspace_id = _parse_workspace_id(x_workspace_id)
    logger.info(
        f"Internal memory ingest-agent - key_workspace: {api_key_auth.workspace_id}, "
        f"workspace: {workspace_id}, end_user_id: {payload.end_user_id}"
    )
    end_user_id = payload.end_user_id
    if end_user_id:
        async with get_async_db_context() as db:
            end_user = await validate_end_user_in_workspace_async(db, end_user_id, workspace_id)
            # 合并路由：改写为合并目标用户，与记忆检索端点的口径一致
            end_user_id = str(end_user.id)

    messages = [
        SimpleNamespace(
            id=_parse_uuid(m.id, "message id") if m.id else None,
            role=m.role,
            content=m.content,
            meta_data=m.meta_data,
            created_at=_parse_iso_created_at(m.created_at),
            should_memorize=m.should_memorize,
        )
        for m in payload.messages
    ]

    await MemoryService.ingest_agent_messages(
        conversation_id=payload.conversation_id,
        messages=messages,
        app_id=payload.app_id,
        config_id=payload.config_id,
        workspace_id=str(workspace_id),
        end_user_id=end_user_id,
        language=payload.language,
        storage_type=payload.storage_type,
        user_rag_memory_id=payload.user_rag_memory_id,
    )
    return success()


@router.get("/memory/workspace-config")
@require_api_key_self_db(scopes=["memory"])
async def internal_memory_workspace_config(
        x_workspace_id: Optional[str] = Header(None, alias="X-Workspace-Id"),
        api_key_auth: ApiKeyAuth = None,
):
    """查询显式工作空间启用中的记忆配置 id。"""
    workspace_id = _parse_workspace_id(x_workspace_id)
    logger.info(
        f"Internal memory workspace-config - key_workspace: {api_key_auth.workspace_id}, "
        f"workspace: {workspace_id}"
    )
    async with get_async_db_context() as db:
        config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(workspace_id)
    return success(data={"config_id": str(config_id)})
