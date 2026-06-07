import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models import User
from app.models.workflow_model import WorkflowExecution
from app.schemas.human_intervention_schema import HumanInterventionSubmitRequest
from app.services.intervention_registry import submit_intervention

router = APIRouter(prefix="/apps", tags=["Human Intervention"])
logger = get_business_logger()


@router.post(
    "/{app_id}/workflow/interventions/{execution_id}/submit",
    summary="提交人工介入响应（通知 SSE 流继续执行）",
)
@cur_workspace_access_guard()
async def submit_human_intervention(
        app_id: uuid.UUID,
        execution_id: str,
        payload: HumanInterventionSubmitRequest,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[User, Depends(get_current_user)],
):
    # Query by execution_id (unique key) first, then validate app relationship.
    # A combined filter (execution_id + app_id) can fail if the app_id in URL
    # doesn't exactly match the stored app_id (e.g. type/format differences).
    execution = db.query(WorkflowExecution).filter(
        WorkflowExecution.execution_id == execution_id,
    ).first()

    if not execution:
        raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

    if execution.app_id != app_id:
        raise BusinessException("无权操作此执行记录", BizCode.FORBIDDEN)

    if execution.status != "waiting_human":
        raise BusinessException(
            f"当前执行状态为 '{execution.status}'，不接受人工介入响应",
            BizCode.BAD_REQUEST,
        )

    if not submit_intervention(execution_id, payload.node_id, payload.action_id, payload.form_data):
        raise BusinessException(
            "未找到等待中的干预请求，可能 SSE 连接已断开",
            BizCode.BAD_REQUEST,
        )

    return success(data={
        "execution_id": execution_id,
        "node_id": payload.node_id,
        "action_id": payload.action_id,
        "form_data": payload.form_data,
    })
