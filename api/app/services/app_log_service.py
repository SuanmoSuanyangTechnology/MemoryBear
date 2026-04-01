"""应用日志服务层"""
import uuid
from typing import Optional, Tuple
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.models.conversation_model import Conversation, Message
from app.repositories.conversation_repository import ConversationRepository, MessageRepository

logger = get_business_logger()


class AppLogService:
    """应用日志服务"""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repository = ConversationRepository(db)
        self.message_repository = MessageRepository(db)

    def list_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        is_draft: Optional[bool] = None,
    ) -> Tuple[list[Conversation], int]:
        """
        查询应用日志会话列表

        Args:
            app_id: 应用 ID
            workspace_id: 工作空间 ID
            page: 页码（从 1 开始）
            pagesize: 每页数量
            is_draft: 是否草稿会话（None 表示不过滤）

        Returns:
            Tuple[list[Conversation], int]: (会话列表，总数)
        """
        logger.info(
            "查询应用日志会话列表",
            extra={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "page": page,
                "pagesize": pagesize,
                "is_draft": is_draft
            }
        )

        # 使用 Repository 查询
        conversations, total = self.conversation_repository.list_app_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            is_draft=is_draft,
            page=page,
            pagesize=pagesize
        )

        logger.info(
            "查询应用日志会话列表成功",
            extra={
                "app_id": str(app_id),
                "total": total,
                "returned": len(conversations)
            }
        )

        return conversations, total

    def get_conversation_detail(
        self,
        app_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID
    ) -> Conversation:
        """
        查询会话详情（包含消息）

        Args:
            app_id: 应用 ID
            conversation_id: 会话 ID
            workspace_id: 工作空间 ID

        Returns:
            Conversation: 包含消息的会话对象

        Raises:
            ResourceNotFoundException: 当会话不存在时
        """
        logger.info(
            "查询应用日志会话详情",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id)
            }
        )

        # 查询会话
        conversation = self.conversation_repository.get_conversation_for_app_log(
            conversation_id=conversation_id,
            app_id=app_id,
            workspace_id=workspace_id
        )

        # 查询消息（按时间正序）
        messages = self.message_repository.get_messages_by_conversation(
            conversation_id=conversation_id
        )

        # 将消息附加到会话对象
        conversation.messages = messages

        logger.info(
            "查询应用日志会话详情成功",
            extra={
                "app_id": str(app_id),
                "conversation_id": str(conversation_id),
                "message_count": len(messages)
            }
        )

        return conversation
