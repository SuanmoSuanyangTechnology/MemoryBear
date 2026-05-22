"""
消息交互控制器

包含：重新生成、点赞/点踩、分享、举报、删除等功能
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models import User, Message
from app.schemas import app_schema
from app.services.conversation_service import ConversationService
from app.services.message_feedback_service import FeedbackService
from app.services.message_report_service import ReportService
from app.services.conversation_share_service import ConversationShareService
from app.services.draft_run_service import AgentRunService
from app.services.app_service import AppService
from app.models import ModelConfig

router = APIRouter(prefix="/apps", tags=["Message Interaction"])


# ========== 重新生成 ==========

@router.post("/{app_id}/messages/{message_id}/regenerate", summary="重新生成回复")
@cur_workspace_access_guard()
async def regenerate_message(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """重新生成 AI 回复，支持多版本切换

    核心逻辑：
    - 保持同一上下文、同一前置对话、同一用户提问
    - 不新增用户消息，只是复用当前会话截止到上一轮的 messages 上下文数组
    - 再次调用 LLM 生成新回答
    - 多版本历史保留、可切换回看
    """
    workspace_id = current_user.current_workspace_id

    # 获取配置
    service = AppService(db)
    agent_cfg = service.get_agent_config(app_id=app_id, workspace_id=workspace_id)
    model_config = None
    if agent_cfg.default_model_config_id:
        model_config = db.get(ModelConfig, agent_cfg.default_model_config_id)

    if not model_config:
        from app.core.exceptions import ResourceNotFoundException
        raise ResourceNotFoundException("模型配置", str(agent_cfg.default_model_config_id))

    # 调用重新生成服务
    draft_service = AgentRunService(db)
    result = await draft_service.regenerate(
        message_id=message_id,
        agent_config=agent_cfg,
        model_config=model_config,
        workspace_id=workspace_id,
        user_id=str(current_user.id),
    )

    return success(data=app_schema.RegenerateResponse(**result))


# ========== 消息版本管理 ==========

@router.get("/{app_id}/messages/{message_id}/versions", summary="获取消息的所有版本")
@cur_workspace_access_guard()
async def list_message_versions(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """获取重新生成的所有历史版本"""
    workspace_id = current_user.current_workspace_id

    conv_service = ConversationService(db)
    versions = conv_service.get_message_versions(message_id)

    return success(data=[app_schema.MessageVersion(**v) for v in versions])


@router.post("/{app_id}/messages/{message_id}/switch-version/{version}", summary="切换消息版本")
@cur_workspace_access_guard()
async def switch_message_version(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        version: int,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """切换展示的消息版本"""
    workspace_id = current_user.current_workspace_id

    conv_service = ConversationService(db)
    result = conv_service.switch_message_version(message_id, version, workspace_id)

    return success(data=result)


# ========== 点赞/点踩 ==========

@router.post("/{app_id}/messages/{message_id}/feedback", summary="提交消息反馈")
@cur_workspace_access_guard()
async def submit_message_feedback(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        payload: app_schema.MessageFeedbackRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """点赞/点踩 AI 回复

    幂等设计：重复点击可切换取消
    - 点赞：用户对满意的 AI 回复给予正面反馈
    - 点踩：用户对不满意的 AI 回复给予负面反馈，可附加文字说明
    """
    workspace_id = current_user.current_workspace_id

    # 获取消息以验证存在性和获取 conversation_id
    message = db.get(Message, message_id)
    if not message:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    feedback_service = FeedbackService(db)
    result = feedback_service.submit_feedback(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        user_id=str(current_user.id),
        feedback_type=payload.feedback_type,
        feedback_content=payload.feedback_content,
    )

    return success(data=app_schema.MessageFeedbackResponse(**result))


@router.get("/{app_id}/messages/{message_id}/feedback", summary="获取用户对消息的反馈")
@cur_workspace_access_guard()
async def get_user_feedback(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """获取当前用户对某条消息的反馈状态"""
    feedback_service = FeedbackService(db)
    result = feedback_service.get_user_feedback(message_id, str(current_user.id))

    return success(data=result)


# ========== 分享会话 ==========

@router.post("/{app_id}/conversations/{conversation_id}/share", summary="分享会话")
@cur_workspace_access_guard()
async def share_conversation(
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        payload: app_schema.ShareConversationRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """生成会话分享链接

    分享链接：生成可公开访问的对话链接，他人点击即可查看完整对话内容（只读视角）
    """
    workspace_id = current_user.current_workspace_id

    share_service = ConversationShareService(db)
    result = share_service.create_share(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        password=payload.password,
        expire_hours=payload.expire_hours,
        allow_copy=payload.allow_copy,
    )

    return success(data=app_schema.ShareConversationResponse(**result))


@router.get("/share/{share_uuid}", summary="访问分享的会话")
async def get_shared_conversation(
        share_uuid: str,
        password: Optional[str] = None,
        db: Session = Depends(get_db),
):
    """通过分享链接访问会话（只读模式，无需登录）

    权限控制：
    - 分享页只读模式：隐藏输入框、重新生成、删除、反馈等操作按钮
    - 支持设置密码访问、有效期、是否允许复制
    """
    share_service = ConversationShareService(db)
    result = share_service.get_shared_conversation(share_uuid, password)

    return success(data=result)


@router.delete("/{app_id}/conversations/{conversation_id}/share/{share_uuid}", summary="撤销分享链接")
@cur_workspace_access_guard()
async def revoke_share(
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        share_uuid: str,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """撤销会话分享链接"""
    workspace_id = current_user.current_workspace_id

    share_service = ConversationShareService(db)
    share_service.revoke_share(share_uuid, workspace_id)

    return success(msg="分享链接已撤销")


@router.get("/{app_id}/conversations/{conversation_id}/shares", summary="列出会话的分享链接")
@cur_workspace_access_guard()
async def list_conversation_shares(
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """列出会话的所有分享链接"""
    workspace_id = current_user.current_workspace_id

    share_service = ConversationShareService(db)
    result = share_service.list_shares(conversation_id, workspace_id)

    return success(data=result)


# ========== 举报 ==========

@router.post("/{app_id}/messages/{message_id}/report", summary="举报消息")
@cur_workspace_access_guard()
async def report_message(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        payload: app_schema.MessageReportRequest,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """举报消息中的违规内容

    支持选中反馈：
    - 用户选中回复中的某段文本，提交举报或反馈，标记不当内容
    - 上报数据包含：message_id + 文本起始偏移、结束偏移
    """
    workspace_id = current_user.current_workspace_id

    # 获取消息以验证存在性
    message = db.get(Message, message_id)
    if not message:
        from app.core.exceptions import BusinessException
        from app.core.error_codes import BizCode
        raise BusinessException("消息不存在", BizCode.NOT_FOUND)

    report_service = ReportService(db)
    result = report_service.submit_report(
        message_id=message_id,
        conversation_id=message.conversation_id,
        workspace_id=workspace_id,
        reported_by=current_user.id,
        report_type=payload.report_type,
        report_reason=payload.report_reason,
        text_start_offset=payload.text_start_offset,
        text_end_offset=payload.text_end_offset,
        selected_text=payload.selected_text,
    )

    return success(data=app_schema.MessageReportResponse(**result))


# ========== 删除消息 ==========

@router.delete("/{app_id}/messages/{message_id}", summary="删除消息")
@cur_workspace_access_guard()
async def delete_message(
        app_id: uuid.UUID,
        message_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
):
    """删除单条消息（逻辑删除）

    删除中间某条消息后，后续会话上下文自动断层，新追问不再携带被删除的消息
    """
    workspace_id = current_user.current_workspace_id

    conv_service = ConversationService(db)
    await conv_service.delete_message(message_id, workspace_id)

    return success(msg="消息已删除")
