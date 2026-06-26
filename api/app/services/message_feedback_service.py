"""
消息反馈服务（点赞/点踩/收藏）
"""
import uuid
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import to_timestamp_ms
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

                # 收藏标记可能挂在同一行：若已收藏则只清空反馈字段、保留行与收藏；
                # 否则删除整行。
                if existing.is_favorite:
                    existing.feedback_type = None
                    existing.feedback_content = None
                else:
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

            # 切换/设置类型：existing.feedback_type 可能为 None（仅收藏行首次点赞/点踩）
            if existing.feedback_type == "like":
                message.like_count = max(0, message.like_count - 1)
            elif existing.feedback_type == "dislike":
                message.dislike_count = max(0, message.dislike_count - 1)
            # existing.feedback_type is None → 无旧类型可递减
            if feedback_type == "like":
                message.like_count += 1
            else:
                message.dislike_count += 1

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
            is_favorite=False,
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
            "created_at": to_timestamp_ms(feedback.created_at),
        }


class FavoriteService:
    """消息收藏服务

    收藏状态复用 message_feedbacks.is_favorite 字段：同一 (message_id, user_id)
    互动行同时承载 like/dislike（互斥、可空）与收藏标记，故点赞与收藏可共存于同一行。
    """

    def __init__(self, db: Session):
        self.db = db

    def toggle_favorite(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str,
    ) -> Dict[str, Any]:
        """切换消息收藏状态（幂等）：已收藏则取消，未收藏则新增。

        - 已有互动行（点赞/点踩/收藏）：
          - 已收藏 → 置 is_favorite=False；若此时 feedback_type 也为空，则删除空行
          - 未收藏 → 置 is_favorite=True
        - 无互动行 → 插入 (feedback_type=None, is_favorite=True) 的新行

        Note: 调用方需保证 message 已存在并传入正确的 conversation_id（controller 已做存在性校验）。
        """
        existing = self.db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        ).first()

        if existing:
            if existing.is_favorite:
                existing.is_favorite = False
                if existing.feedback_type is None:
                    # 既无 like/dislike 也无收藏 → 清理空行，保持表干净
                    self.db.delete(existing)
                self.db.commit()
                logger.info(
                    "取消收藏",
                    extra={"message_id": str(message_id), "user_id": user_id},
                )
                return {"action": "cancelled", "is_favorited": False}

            existing.is_favorite = True
            self.db.commit()
            logger.info(
                "收藏消息",
                extra={"message_id": str(message_id), "user_id": user_id},
            )
            return {"action": "created", "is_favorited": True}

        favorite = MessageFeedback(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            feedback_type=None,
            feedback_content=None,
            is_favorite=True,
        )
        self.db.add(favorite)
        self.db.commit()
        logger.info(
            "收藏消息",
            extra={"message_id": str(message_id), "user_id": user_id},
        )
        return {"action": "created", "is_favorited": True}
