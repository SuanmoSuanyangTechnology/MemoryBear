"""应用日志（消息记录）接口"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.schemas.app_log_schema import AppLogConversation, AppLogConversationDetail
from app.schemas.response_schema import PageData, PageMeta
from app.services.app_service import AppService
from app.services.app_log_service import AppLogService

router = APIRouter(prefix="/apps", tags=["App Logs"])
logger = get_business_logger()


@router.get("/{app_id}/logs", summary="应用日志 - 会话列表")
@cur_workspace_access_guard()
def list_app_logs(
        app_id: uuid.UUID,
        page: int = Query(1, ge=1),
        pagesize: int = Query(20, ge=1, le=100),
        is_draft: Optional[bool] = None,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """查看应用下所有会话记录（分页）

    - 支持按 is_draft 筛选（草稿会话 / 发布会话）
    - 按最新更新时间倒序排列
    - 所有人（包括共享者和被共享者）都只能查看自己的会话记录
    """
    workspace_id = current_user.current_workspace_id

    # 验证应用访问权限
    app_service = AppService(db)
    app_service.get_app(app_id, workspace_id)

    # 使用 Service 层查询
    log_service = AppLogService(db)
    conversations, total = log_service.list_conversations(
        app_id=app_id,
        workspace_id=workspace_id,
        page=page,
        pagesize=pagesize,
        is_draft=is_draft
    )

    items = [AppLogConversation.model_validate(c) for c in conversations]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)

    return success(data=PageData(page=meta, items=items))


@router.get("/{app_id}/logs/{conversation_id}", summary="应用日志 - 会话消息详情")
@cur_workspace_access_guard()
def get_app_log_detail(
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """查看某会话的完整消息记录

    - 返回会话基本信息 + 所有消息（按时间正序）
    - 消息 meta_data 包含模型名、token 用量等信息
    - 所有人（包括共享者和被共享者）都只能查看自己的会话详情
    """
    workspace_id = current_user.current_workspace_id

    # 验证应用访问权限
    app_service = AppService(db)
    app_service.get_app(app_id, workspace_id)

    # 使用 Service 层查询
    log_service = AppLogService(db)
    conversation = log_service.get_conversation_detail(
        app_id=app_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id
    )

    detail = AppLogConversationDetail.model_validate(conversation)

    return success(data=detail)
