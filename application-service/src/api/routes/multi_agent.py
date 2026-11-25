"""Multi-agent config routes.

Mirrors the monolith ``multi_agent_controller`` definition-plane endpoints:
config read, config update, the handoff routing preflight and the handoffs
chat / stream / history / clear surface.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...db import get_async_db_context, get_db_context
from ...guards.workspace_access import require_workspace_access
from ...i18n import get_current_locale
from ...integrations.model.errors import ModelInvokeCancelledError
from ...schemas.multi_agent_schema import (
    MultiAgentConfigSchema,
    MultiAgentConfigUpdate,
    MultiAgentRunRequest,
)
from ...services.app_service import AppService
from ...services.chat_errors import error_frame, error_payload
from ...services.multi_agent_handoffs import MultiAgentHandoffsService
from ...services.multi_agent_service import MultiAgentService, default_multi_agent_config
from ...services.workspace_service import get_workspace_storage_type_without_auth_async
from ...trace import get_trace_id
from ...utils.sse_stream import sse_response
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/apps", tags=["Multi-Agent"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]


@router.get(
    "/{app_id}/multi-agent",
    summary="获取当前应用的最新有效多 Agent 配置",
    response_model=ApiResponse,
)
def get_multi_agent_configs(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    principal: Caller,
):
    """Get the app's latest active multi-agent config, or the default template"""
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    service = MultiAgentService(db)

    # Fetch the latest active config by app_id (agent_id already mapped to app_id)
    config = service.get_multi_agent_configs(app_id)

    if not config:
        # Shared with the v1 face (`/v1/apps/{id}/app-config?type=multi_agent`);
        # an inline copy here would let the two faces drift apart silently.
        return success(
            data=default_multi_agent_config(app_id),
            msg="该应用暂无配置，返回默认模板"
        )

    # config is already a dict; return it directly
    return success(data=config)


@router.put(
    "/{app_id}/multi-agent",
    summary="更新多 Agent 配置",
    response_model=ApiResponse,
)
def update_multi_agent_config(
    app_id: UUID,
    data: MultiAgentConfigUpdate,
    workspace_id: WorkspaceId,
    db: DbSession,
    principal: Caller,
):
    """Update multi-agent config"""
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_writable(app, workspace_id)

    config = MultiAgentService(db).update_config(app_id, data)
    return success(
        data=MultiAgentConfigSchema.model_validate(config),
        msg="多 Agent 配置更新成功"
    )


@router.post(
    "/{app_id}/handoffs/test-routing",
    summary="测试 Handoff 路由决策",
    response_model=ApiResponse,
)
def test_handoff_routing(
    app_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
    principal: Caller,
    message: str = Query(..., description="测试消息"),
):
    """Test the Handoff routing decision (no actual execution)

    For debugging and testing the routing logic; returns:
    - initial_agent_id: the initially selected Agent
    - available_handoff_tools: the available handoff tool list
    - handoff_suggestion: the automatic handoff suggestion

    Does not actually run any Agent; only the routing decision is returned
    """
    app_service = AppService(db)
    app = app_service._get_app_or_404(app_id)
    app_service._validate_app_accessible(app, workspace_id)

    handoffs_service = MultiAgentHandoffsService(db, MultiAgentService(db))

    result = handoffs_service.test_handoff_routing(app_id, message)

    return success(
        data=result,
        msg="路由测试完成"
    )


async def _prepare_handoffs_memory_context_async(
    *,
    workspace_id: UUID,
) -> tuple[str, str]:
    """Resolve memory storage type for the handoffs stream.

    RAG memory no longer depends on the USER_RAG_MERORY knowledge base; the
    returned knowledge id is always empty. Workspace access was already
    enforced by the WorkspaceId dependency.
    """
    async with get_async_db_context() as db:
        storage_type = await get_workspace_storage_type_without_auth_async(
            db=db,
            workspace_id=workspace_id,
        )
        if storage_type is None:
            storage_type = "neo4j"
    return storage_type, ""


@router.post(
    "/{app_id}/chat/handoffs",
    summary="支持 Agent Handoffs 的聊天接口",
    response_model=ApiResponse,
)
async def chat_with_handoffs(
    app_id: UUID,
    request: MultiAgentRunRequest,
    workspace_id: WorkspaceId,
):
    """Run one multi-agent turn in collaborative mode (non-streaming)."""
    # The host route had no workspace check; the service adds it deliberately
    # so both handoffs surfaces match every other app route here.
    with get_db_context() as db:
        app_service = AppService(db)
        app = app_service._get_app_or_404(app_id)
        app_service._validate_app_accessible(app, workspace_id)

    async with get_async_db_context() as db:
        result = await MultiAgentService(db).run(app_id, request)

    return success(data=result, msg="Agent Handoffs 执行成功")


@router.post(
    "/{app_id}/chat/handoffs/stream",
    summary="流式 Agent Handoffs 聊天接口",
)
async def chat_with_handoffs_stream(
    app_id: UUID,
    request: MultiAgentRunRequest,
    workspace_id: WorkspaceId,
):
    """Run one multi-agent turn in collaborative mode as an SSE stream."""
    with get_db_context() as db:
        app_service = AppService(db)
        app = app_service._get_app_or_404(app_id)
        app_service._validate_app_accessible(app, workspace_id)

    storage_type, user_rag_memory_id = await _prepare_handoffs_memory_context_async(
        workspace_id=workspace_id,
    )

    async def event_generator():
        try:
            async with get_async_db_context() as stream_db:
                async for event in MultiAgentService(stream_db).run_stream(
                    app_id=app_id,
                    request=request,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                ):
                    yield event
        except ModelInvokeCancelledError:
            logger.info(
                "Handoffs 流式执行取消", extra={"app_id": str(app_id)}
            )
        except Exception as exc:
            # 200 is already committed once streaming starts; without a
            # terminal error frame the client sees an empty stream.
            logger.error(
                "Handoffs 流式执行失败",
                extra={"app_id": str(app_id), "error": str(exc)},
            )
            yield error_frame(
                error_payload(
                    exc,
                    locale=get_current_locale(),
                    trace_id=get_trace_id(),
                )
            )

    return sse_response(event_generator())


@router.get(
    "/conversations/{conversation_id}/handoffs/history",
    summary="获取会话的 Handoff 历史",
    response_model=ApiResponse,
)
def get_handoff_history(
    conversation_id: str,
    db: DbSession,
):
    """Return the handoff history recorded in the process-local manager."""
    handoffs_service = MultiAgentHandoffsService(db, MultiAgentService(db))
    history = handoffs_service.get_handoff_history(conversation_id)

    if not history:
        return success(data=None, msg="该会话没有 Handoff 历史记录")

    return success(data=history, msg="获取 Handoff 历史成功")


@router.delete(
    "/conversations/{conversation_id}/handoffs",
    summary="清除会话的 Handoff 状态",
    response_model=ApiResponse,
)
def clear_handoff_state(
    conversation_id: str,
    db: DbSession,
):
    """Clear the in-memory handoff state for one conversation."""
    handoffs_service = MultiAgentHandoffsService(db, MultiAgentService(db))
    handoffs_service.clear_handoff_state(conversation_id)
    return success(msg="Handoff 状态已清除")
