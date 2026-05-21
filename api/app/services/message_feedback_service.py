"""
消息反馈服务（点赞/点踩）
"""
import uuid
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.models import MessageFeedback, Message

logger = get_business_logger()


class FeedbackService:
    """消息反馈服务"""

    def __init__(self, db: Session):
        self.db = db

    def submit_feedback(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str,
        feedback_type: str,
        feedback_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """提交反馈（点赞/点踩），幂等设计

        Args:
            message_id: 消息ID
            conversation_id: 会话ID
            workspace_id: 工作空间ID
            user_id: 用户ID
            feedback_type: 反馈类型 (like/dislike)
            feedback_content: 反馈内容（点踩时填写原因）

        Returns:
            Dict: 包含操作结果
        """
        # 查找已有反馈
        existing = self.db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        ).first()

        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        if existing:
            # 重复点击：取消反馈
            if existing.feedback_type == feedback_type:
                # 更新计数
                if feedback_type == "like":
                    message.like_count = max(0, message.like_count - 1)
                else:
                    message.dislike_count = max(0, message.dislike_count - 1)

                self.db.delete(existing)
                self.db.commit()
                logger.info(
                    "取消反馈",
                    extra={
                        "message_id": str(message_id),
                        "user_id": user_id,
                        "feedback_type": feedback_type,
                    }
                )
                return {"action": "cancelled", "feedback_type": None}

            # 切换类型：like -> dislike 或 dislike -> like
            if existing.feedback_type == "like":
                message.like_count = max(0, message.like_count - 1)
                message.dislike_count += 1
            else:
                message.dislike_count = max(0, message.dislike_count - 1)
                message.like_count += 1

            existing.feedback_type = feedback_type
            existing.feedback_content = feedback_content
            self.db.commit()
            logger.info(
                "更新反馈",
                extra={
                    "message_id": str(message_id),
                    "user_id": user_id,
                    "feedback_type": feedback_type,
                }
            )
            return {"action": "updated", "feedback_type": feedback_type}

        # 新增反馈
        feedback = MessageFeedback(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            feedback_type=feedback_type,
            feedback_content=feedback_content,
        )
        self.db.add(feedback)

        # 更新计数
        if feedback_type == "like":
            message.like_count += 1
        else:
            message.dislike_count += 1

        self.db.commit()
        logger.info(
            "创建反馈",
            extra={
                "message_id": str(message_id),
                "user_id": user_id,
                "feedback_type": feedback_type,
            }
        )
        return {"action": "created", "feedback_type": feedback_type}

    def get_feedback_statistics(
        self,
        message_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """获取消息的反馈统计

        Args:
            message_id: 消息ID

        Returns:
            Dict: 统计数据
        """
        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        return {
            "message_id": str(message_id),
            "like_count": message.like_count,
            "dislike_count": message.dislike_count,
            "report_count": message.report_count,
        }

    def get_user_feedback(
        self,
        message_id: uuid.UUID,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """获取用户对消息的反馈

        Args:
            message_id: 消息ID
            user_id: 用户ID

        Returns:
            Optional[Dict]: 反馈信息，如果没有则返回 None
        """
        feedback = self.db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        ).first()

        if not feedback:
            return None

        return {
            "feedback_type": feedback.feedback_type,
            "feedback_content": feedback.feedback_content,
            "created_at": int(feedback.created_at.timestamp() * 1000),
        }
