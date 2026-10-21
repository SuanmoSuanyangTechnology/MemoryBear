"""Human-intervention submit routes (frozen port of the monolith
``human_intervention_controller`` plus the ``/v1`` submit face of
``service/app_api_controller``).

Two faces live in this module: the manager face (``/apps``, workspace-guarded)
and the ``/v1`` API-key face (``/app``); the gateway rewrites both onto
``/internal/v1``. Both submit an operator action for a waiting execution and
wake the paused SSE stream through the in-process intervention registry, which
is why these routes only work in the process that runs the stream (the host
has the same constraint).

The manager face re-applies the orphan-app and workspace-ownership checks the
host only carries on its ``/v1`` face: without them a console caller could
drive an execution whose app was deleted or that belongs to another workspace
(the host checks the URL app id only). The service treats those as
cross-tenant gaps and closes them on both faces.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...errors import BizCode, BusinessException
from ...guards.workspace_access import require_workspace_access
from ...schemas.app_schema import HumanInterventionRequest
from ...services.intervention_registry import submit_intervention
from ...services.workflow_service import WorkflowService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse
from .annotations import _resolve_v1_app
from .messages import _require_app_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["Human Intervention"])
v1_router = APIRouter(prefix="/app", tags=["Human Intervention API (v1)"])

DbSession = Annotated[Session, Depends(get_sync_db)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]
Caller = Annotated[Principal, Depends(get_principal)]


def _resolve_waiting_execution(
    db: Session,
    *,
    execution_id: str,
    app_id: UUID,
    workspace_id: UUID,
):
    """Shared resolution chain: exists -> app match -> not orphaned ->
    workspace match -> waiting state. Raises with the host's messages."""

    execution = WorkflowService(db).get_execution(execution_id)
    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    if execution.app_id != app_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    # A deleted app (or a runtime-only execution without one) must not reach
    # the relationship below; the host's /v1 face guards this, the console face
    # does not.
    if execution.app is None:
        raise BusinessException("执行记录关联的应用不存在", BizCode.NOT_FOUND)

    if execution.app.workspace_id != workspace_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    if execution.status != "waiting_human":
        raise BusinessException(
            f"当前执行状态为 '{execution.status}'，不接受人工介入响应",
            BizCode.BAD_REQUEST,
        )
    return execution


def _submit(execution_id: str, payload: HumanInterventionRequest) -> dict:
    if not submit_intervention(
        execution_id, payload.node_id, payload.action_id, payload.form_data
    ):
        raise BusinessException(
            "未找到等待中的干预请求，可能 SSE 连接已断开",
            BizCode.BAD_REQUEST,
        )
    return {
        "execution_id": execution_id,
        "node_id": payload.node_id,
        "action_id": payload.action_id,
        "form_data": payload.form_data,
    }


@router.post(
    "/{app_id}/workflow/interventions/{execution_id}/submit",
    summary="提交人工介入响应（通知 SSE 流继续执行）",
    response_model=ApiResponse,
)
def submit_human_intervention(
    app_id: UUID,
    execution_id: str,
    payload: HumanInterventionRequest,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Submit the console operator's response for a waiting execution."""

    _resolve_waiting_execution(
        db, execution_id=execution_id, app_id=app_id, workspace_id=workspace_id
    )
    return success(data=_submit(execution_id, payload))


@v1_router.post(
    "/workflow/interventions/{execution_id}/submit",
    summary="提交人工介入响应（API Key 认证，通知 SSE 流继续执行）",
    response_model=ApiResponse,
)
def submit_human_intervention_v1(
    request: Request,
    execution_id: str,
    payload: HumanInterventionRequest,
    principal: Caller,
    db: DbSession,
):
    """Submit the API-key caller's response for a waiting execution."""

    execution_id = execution_id.strip()
    if not execution_id:
        raise BusinessException("execution_id 不能为空", BizCode.INVALID_PARAMETER)

    app_id = _require_app_id(_resolve_v1_app(request, db, principal))
    _resolve_waiting_execution(
        db,
        execution_id=execution_id,
        app_id=app_id,
        workspace_id=principal.workspace_id,
    )
    return success(data=_submit(execution_id, payload))


__all__ = ["router", "v1_router"]
