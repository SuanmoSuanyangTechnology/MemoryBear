"""应用日志（消息记录）接口"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models.conversation_model import Conversation, Message
from app.schemas.app_log_schema import AppLogConversation, AppLogConversationDetail, AppLogMessage
from app.schemas.response_schema import PageData, PageMeta
from app.services.app_service import AppService

router = APIRouter(prefix="/apps", tags=["App Logs"])
logger = get_business_logger()


@router.get("/{app_id}/logs", summary="应用日志 - 会话列表")
@cur_workspace_access_guard()
def list_app_logs(
        app_id: uuid.UUID,
        page: int = Query(1, ge=1),
        pagesize: int = Query(20, ge=1, le=100),
        user_id: Optional[str] = None,
        is_draft: Optional[bool] = None,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """查看应用下所有会话记录（分页）

    - 支持按 user_id 筛选
    - 支持按 is_draft 筛选（草稿会话 / 发布会话）
    - 按最新更新时间倒序排列
    - 所有人（包括共享者和被共享者）都只能查看自己的会话记录
    """
    workspace_id = current_user.current_workspace_id

    # 验证应用访问权限
    service = AppService(db)
    service.get_app(app_id, workspace_id)

    stmt = select(Conversation).where(
        Conversation.app_id == app_id,
        Conversation.workspace_id == workspace_id,
        Conversation.is_active.is_(True),
    )
    
    # 所有人只能查看自己的会话记录
    stmt = stmt.where(Conversation.user_id == str(current_user.id))

    if user_id:
        stmt = stmt.where(Conversation.user_id == user_id)

    if is_draft is not None:
        stmt = stmt.where(Conversation.is_draft == is_draft)

    total = int(db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one())

    stmt = stmt.order_by(desc(Conversation.updated_at))
    stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)

    conversations = list(db.scalars(stmt).all())

    items = [AppLogConversation.model_validate(c) for c in conversations]
    meta = PageMeta(page=page, pagesize=pagesize, total=total, hasnext=(page * pagesize) < total)

    logger.info(
        "查询应用日志会话列表",
        extra={"app_id": str(app_id), "total": total, "page": page}
    )

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
    service = AppService(db)
    service.get_app(app_id, workspace_id)

    # 查询会话（确保属于该应用和工作空间）
    conversation = db.scalars(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.is_active.is_(True),
            Conversation.user_id == str(current_user.id),
        )
    ).first()

    if not conversation:
        from app.core.exceptions import ResourceNotFoundException
        raise ResourceNotFoundException("会话", str(conversation_id))

    # 查询消息（按时间正序）
    messages = list(db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    ).all())

    detail = AppLogConversationDetail.model_validate(conversation)
    detail.messages = [AppLogMessage.model_validate(m) for m in messages]

    logger.info(
        "查询应用日志会话详情",
        extra={
            "app_id": str(app_id),
            "conversation_id": str(conversation_id),
            "message_count": len(messages)
        }
    )

    return success(data=detail)
