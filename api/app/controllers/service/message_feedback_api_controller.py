"""App 消息反馈服务接口 - 基于 API Key 认证（点赞/点踩，不含收藏）"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.api_key_auth import require_api_key
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.models import Conversation, Message
from app.schemas import app_schema, conversation_schema
from app.schemas.api_key_schema import ApiKeyAuth
from app.services.conversation_service import ConversationService, get_conversation_service
from app.services.message_feedback_service import FeedbackService

router = APIRouter(prefix="/app", tags=["V1 - App Message Feedback"])
logger = get_business_logger()


@router.post("/messages/{message_id}/feedback", summary="提交消息反馈（点赞/点踩）")
@require_api_key(scopes=["app"])
async def submit_message_feedback(
        request: Request,
        message_id: uuid.UUID,
        user_id: str = Query(..., description="外部系统用户 ID（other_id）"),
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
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

    # 解析终端用户（不自动创建，不存在则拒绝，与 list_v1_conversation_messages 一致）
    end_user_id = ConversationService(db)._resolve_v1_internal_user_id(
        workspace_id=workspace_id,
        external_user_id=user_id,
    )
    if end_user_id is None:
        raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

    # 取消息并校验存在性 / 软删
    message = db.get(Message, message_id)
    if not message or message.is_deleted:
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    conversation = db.get(Conversation, message.conversation_id)
    if not conversation:
        raise BusinessException("会话不存在", BizCode.NOT_FOUND)

    # 归属校验：app + workspace + 会话归属者 + 状态（与 list_v1_conversation_messages 对齐）
    if (
        conversation.app_id != app_id
        or conversation.workspace_id != workspace_id
        or conversation.user_id != end_user_id
        or conversation.is_active is not True
        or conversation.is_draft is not False
    ):
        raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

    result = FeedbackService(db).submit_feedback(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=end_user_id,
        feedback_type=payload.feedback_type,
        feedback_content=payload.feedback_content,
    )

    return success(data=app_schema.MessageFeedbackResponse(**result).model_dump(mode="json"))


@router.get(
    "/conversations/{conversation_id}/messages/feedbacks",
    summary="获取会话下消息的点赞与反馈",
)
@require_api_key(scopes=["app"])
async def get_conversation_feedback(
        request: Request,
        conversation_id: uuid.UUID,
        user_id: str = Query(..., description="外部系统用户 ID（other_id）"),
        limit: int = Query(50, ge=1, le=200, description="返回消息数量，最大 200"),
        api_key_auth: ApiKeyAuth = None,
        db: Session = Depends(get_db),
        conversation_service: Annotated[ConversationService, Depends(get_conversation_service)] = None,
):
    """获取会话下所有消息的反馈状态（供前端渲染）

    返回每条消息的 like_count/dislike_count/report_count 及当前用户的 feedback_type。
    不含 is_favorite（收藏功能不在本接口范围）。
    user_id 为外部系统用户标识（other_id），后端解析为终端用户 end_user.id，不存在则拒绝。
    """
    result = conversation_service.list_v1_conversation_feedback(
        app_id=api_key_auth.resource_id,
        workspace_id=api_key_auth.workspace_id,
        external_user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )
    return success(
        data=conversation_schema.V1ConversationFeedbackListResponse(
            **result
        ).model_dump(mode="json")
    )
