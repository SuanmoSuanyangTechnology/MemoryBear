"""
消息收藏服务
"""
import uuid
from typing import Dict, Any

from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.models import MessageFavorite, Message

logger = get_business_logger()


class FavoriteService:
    """消息收藏服务"""

    def __init__(self, db: Session):
        self.db = db

    def toggle_favorite(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str,
        source: str = "pilot_run",
    ) -> Dict[str, Any]:
        """切换消息收藏状态（幂等）

        - 若已收藏则取消收藏
        - 若未收藏则新增收藏

        Args:
            message_id: 消息ID
            conversation_id: 会话ID
            workspace_id: 工作空间ID
            user_id: 用户ID
            source: 来源场景 pilot_run/share

        Returns:
            Dict: {"action": "created" | "cancelled", "is_favorited": bool}
        """
        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        existing = self.db.query(MessageFavorite).filter(
            MessageFavorite.message_id == message_id,
            MessageFavorite.user_id == user_id,
        ).first()

        if existing:
            # 已收藏 → 取消
            self.db.delete(existing)
            self.db.commit()
            logger.info(
                "取消收藏",
                extra={
                    "message_id": str(message_id),
                    "user_id": user_id,
                    "source": source,
                }
            )
            return {"action": "cancelled", "is_favorited": False}

        # 新增收藏
        favorite = MessageFavorite(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            source=source,
        )
        self.db.add(favorite)
        self.db.commit()
        logger.info(
            "收藏消息",
            extra={
                "message_id": str(message_id),
                "user_id": user_id,
                "source": source,
            }
        )
        return {"action": "created", "is_favorited": True}

    def is_favorited(
        self,
        message_id: uuid.UUID,
        user_id: str,
    ) -> bool:
        """查询用户是否已收藏某条消息"""
        row = self.db.query(MessageFavorite).filter(
            MessageFavorite.message_id == message_id,
            MessageFavorite.user_id == user_id,
        ).first()
        return row is not None

