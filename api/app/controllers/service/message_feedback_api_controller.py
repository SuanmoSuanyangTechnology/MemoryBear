"""App 消息反馈服务接口 - 基于 API Key 认证（点赞/点踩，不含收藏）"""
import uuid

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_key_auth import require_api_key_self_db
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_async_db
from app.models import Conversation, Message, MessageFeedback
from app.models.end_user_model import EndUser
from app.schemas import app_schema, conversation_schema
from app.schemas.api_key_schema import ApiKeyAuth

router = APIRouter(prefix="/app", tags=["V1 - App Message Feedback"])
logger = get_business_logger()


async def _resolve_v1_internal_user_id(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    external_user_id: str,
) -> str | None:
    """将外部 user_id 解析为内部终端用户 ID，不存在时返回 None。"""
    result = await db.execute(
        select(EndUser.id)
        .where(
            EndUser.workspace_id == workspace_id,
            EndUser.other_id == external_user_id,
            EndUser.is_active.is_(True),
        )
        .order_by(EndUser.created_at.asc())
        .limit(1)
    )
    end_user_id = result.scalar_one_or_none()
    return str(end_user_id) if end_user_id else None


@router.post("/messages/{message_id}/feedback", summary="提交消息反馈（点赞/点踩）")
@require_api_key_self_db(scopes=["app"])
async def submit_message_feedback(
    request: Request,
    message_id: uuid.UUID,
    user_id: str = Query(..., description="外部系统用户 ID（other_id）"),
    api_key_auth: ApiKeyAuth = None,
    db: AsyncSession = Depends(get_async_db),
    body_placeholder: str = Body(None, description="占位参数，实际请求体通过 request.json() 解析"),
):
    """点赞/点踩 AI 回复（v1 对外，API Key 认证）

    幂等设计：重复点击同类型取消反馈；like/dislike 互斥切换。
    user_id 为外部系统用户标识（other_id），后端解析为终端用户 end_user.id，不存在则拒绝。

    请求体无需 payload 包裹，直接传 {"feedback_type": "like|dislike", "feedback_content": "..."}。
    与 /v1/app/chat、/v1/memory/read 等接口一致：标量 body 占位 + request.json() 手动解析，
    避免 api_key_auth（Pydantic 模型）与 body 模型共存时 FastAPI 嵌入式校验导致的包裹要求。
    """
    payload = app_schema.MessageFeedbackRequest(**(await request.json()))

    if not user_id:
        raise BusinessException("user_id 不能为空", BizCode.INVALID_PARAMETER)

    workspace_id = api_key_auth.workspace_id
    app_id = api_key_auth.resource_id

    end_user_id = await _resolve_v1_internal_user_id(db, workspace_id, user_id)
    if end_user_id is None:
        raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

    message = await db.get(Message, message_id)
    if not message or message.is_deleted:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    conversation = await db.get(Conversation, message.conversation_id)
    if not conversation:
        raise BusinessException("会话不存在", BizCode.NOT_FOUND)

    if (
        conversation.app_id != app_id
        or conversation.workspace_id != workspace_id
        or conversation.user_id != end_user_id
        or conversation.is_active is not True
        or conversation.is_draft is not False
    ):
        raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

    existing = await db.scalar(
        select(MessageFeedback).where(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == end_user_id,
        )
    )

    if existing and existing.feedback_type == payload.feedback_type:
        if payload.feedback_type == "like":
            message.like_count = max(0, message.like_count - 1)
        else:
            message.dislike_count = max(0, message.dislike_count - 1)

        if existing.is_favorite:
            existing.feedback_type = None
            existing.feedback_content = None
        else:
            await db.delete(existing)
        action = "cancelled"
        feedback_type = None
    elif existing:
        if existing.feedback_type == "like":
            message.like_count = max(0, message.like_count - 1)
        elif existing.feedback_type == "dislike":
            message.dislike_count = max(0, message.dislike_count - 1)

        if payload.feedback_type == "like":
            message.like_count += 1
        else:
            message.dislike_count += 1

        existing.feedback_type = payload.feedback_type
        existing.feedback_content = payload.feedback_content
        action = "updated"
        feedback_type = payload.feedback_type
    else:
        db.add(MessageFeedback(
            message_id=message_id,
            conversation_id=message.conversation_id,
            workspace_id=workspace_id,
            user_id=end_user_id,
            feedback_type=payload.feedback_type,
            feedback_content=payload.feedback_content,
            is_favorite=False,
        ))
        if payload.feedback_type == "like":
            message.like_count += 1
        else:
            message.dislike_count += 1
        action = "created"
        feedback_type = payload.feedback_type

    await db.commit()
    logger.info(
        "提交消息反馈",
        extra={
            "message_id": str(message_id),
            "user_id": end_user_id,
            "feedback_type": feedback_type,
            "action": action,
        },
    )
    result = {"action": action, "feedback_type": feedback_type}
    return success(data=app_schema.MessageFeedbackResponse(**result).model_dump(mode="json"))


@router.get(
    "/conversations/{conversation_id}/messages/feedbacks",
    summary="获取会话下消息的点赞与反馈",
)
@require_api_key_self_db(scopes=["app"])
async def get_conversation_feedback(
    request: Request,
    conversation_id: uuid.UUID,
    user_id: str = Query(..., description="外部系统用户 ID（other_id）"),
    limit: int = Query(50, ge=1, le=200, description="返回消息数量，最大 200"),
    api_key_auth: ApiKeyAuth = None,
    db: AsyncSession = Depends(get_async_db),
):
    """获取会话下所有消息的反馈状态（供前端渲染）

    返回每条消息的反馈状态，不含 is_favorite（收藏功能不在本接口范围）。
    user_id 为外部系统用户标识（other_id），后端解析为终端用户 end_user.id，不存在则拒绝。
    """
    if not user_id:
        raise BusinessException("user_id 不能为空", BizCode.INVALID_PARAMETER)

    internal_user_id = await _resolve_v1_internal_user_id(
        db, api_key_auth.workspace_id, user_id
    )
    if internal_user_id is None:
        raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise BusinessException("会话不存在", BizCode.NOT_FOUND)

    if (
        conversation.app_id != api_key_auth.resource_id
        or conversation.workspace_id != api_key_auth.workspace_id
        or conversation.user_id != internal_user_id
        or conversation.is_active is not True
        or conversation.is_draft is not False
    ):
        raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

    message_result = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_not(True),
            Message.is_current.is_(True),
        )
        .order_by(Message.created_at)
        .limit(limit)
    )
    visible = [message for message in message_result.scalars().all() if message.role != "system"]

    feedback_type_map = {}
    feedback_content_map = {}
    if visible:
        feedback_result = await db.execute(
            select(
                MessageFeedback.message_id,
                MessageFeedback.feedback_type,
                MessageFeedback.feedback_content,
            ).where(
                MessageFeedback.message_id.in_([message.id for message in visible]),
                MessageFeedback.user_id == internal_user_id,
            )
        )
        rows = feedback_result.all()
        feedback_type_map = {row[0]: row[1] for row in rows}
        feedback_content_map = {row[0]: row[2] for row in rows}

    result = {
        "conversation_id": conversation.id,
        "items": [
            {
                "message_id": message.id,
                "role": message.role,
                "feedback_type": feedback_type_map.get(message.id),
                "feedback_content": feedback_content_map.get(message.id),
                "created_at": message.created_at,
            }
            for message in visible
        ],
        "limit": limit,
    }
    return success(
        data=conversation_schema.V1ConversationFeedbackListResponse(
            **result
        ).model_dump(mode="json")
    )
