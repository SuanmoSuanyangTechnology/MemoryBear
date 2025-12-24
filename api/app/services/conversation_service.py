"""会话服务"""
import uuid
from typing import Optional, List, Tuple, Annotated

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db import get_db
from app.models import Conversation, Message
from app.core.exceptions import ResourceNotFoundException, BusinessException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class ConversationService:
    """会话服务"""

    def __init__(self, db: Session):
        self.db = db

    def create_conversation(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: Optional[str] = None,
        title: Optional[str] = None,
        is_draft: bool = False,
        config_snapshot: Optional[dict] = None
    ) -> Conversation:
        """创建会话"""
        conversation = Conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            title=title or "新会话",
            is_draft=is_draft,
            config_snapshot=config_snapshot
        )

        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        logger.info(
            "创建会话成功",
            extra={
                "conversation_id": str(conversation.id),
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "is_draft": is_draft
            }
        )

        return conversation

    def get_conversation(
        self,
        conversation_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID] = None
    ) -> Conversation:
        """获取会话"""
        stmt = select(Conversation).where(Conversation.id == conversation_id)

        if workspace_id:
            stmt = stmt.where(Conversation.workspace_id == workspace_id)

        conversation = self.db.scalars(stmt).first()

        if not conversation:
            raise ResourceNotFoundException("会话", str(conversation_id))

        return conversation

    def list_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: Optional[str] = None,
        is_draft: Optional[bool] = None,
        page: int = 1,
        pagesize: int = 20
    ) -> Tuple[List[Conversation], int]:
        """列出会话"""
        stmt = select(Conversation).where(
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.is_active == True
        )

        if user_id:
            stmt = stmt.where(Conversation.user_id == user_id)

        if is_draft is not None:
            stmt = stmt.where(Conversation.is_draft == is_draft)

        # 总数
        count_stmt = stmt.with_only_columns(Conversation.id)
        total = len(self.db.execute(count_stmt).all())

        # 分页
        stmt = stmt.order_by(desc(Conversation.updated_at))
        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)

        conversations = list(self.db.scalars(stmt).all())

        return conversations, total

    def add_message(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        meta_data: Optional[dict] = None
    ) -> Message:
        """添加消息"""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            meta_data=meta_data
        )

        self.db.add(message)

        # 更新会话的消息计数和更新时间
        conversation = self.get_conversation(conversation_id)
        conversation.message_count += 1

        # 如果是第一条用户消息，可以用它作为标题
        if conversation.message_count == 1 and role == "user":
            conversation.title = content[:50] + ("..." if len(content) > 50 else "")

        self.db.commit()
        self.db.refresh(message)

        return message

    def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: Optional[int] = None
    ) -> List[Message]:
        """获取会话消息"""
        stmt = select(Message).where(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at)

        if limit:
            stmt = stmt.limit(limit)

        messages = list(self.db.scalars(stmt).all())

        return messages

    def get_conversation_history(
        self,
        conversation_id: uuid.UUID,
        max_history: Optional[int] = None
    ) -> List[dict]:
        """获取会话历史消息

        Args:
            conversation_id: 会话ID
            max_history: 最大历史消息数量

        Returns:
            List[dict]: 历史消息列表，格式为 [{"role": "user", "content": "..."}, ...]
        """
        messages = self.get_messages(conversation_id, limit=max_history)

        # 转换为字典格式
        history = [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in messages
        ]

        return history

    def save_conversation_messages(
        self,
        conversation_id: uuid.UUID,
        user_message: str,
        assistant_message: str
    ):
        """保存会话消息（用户消息和助手回复）"""
        # 添加用户消息
        self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=user_message
        )

        # 添加助手消息
        self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_message
        )

        logger.debug(
            "保存会话消息成功",
            extra={
                "conversation_id": str(conversation_id),
                "user_message_length": len(user_message),
                "assistant_message_length": len(assistant_message)
            }
        )

    def delete_conversation(
        self,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID
    ):
        """删除会话（软删除）"""
        conversation = self.get_conversation(conversation_id, workspace_id)
        conversation.is_active = False

        self.db.commit()

        logger.info(
            "删除会话成功",
            extra={
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id)
            }
        )

    def create_or_get_conversation(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        is_draft: bool = False,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
    ) -> Conversation:
        """创建或获取会话"""

        # 如果提供了 conversation_id，尝试获取现有会话
        if conversation_id:
            try:
                conversation = self.get_conversation(
                    conversation_id=conversation_id,
                    workspace_id=workspace_id
                )

                # 验证会话是否属于该应用
                if conversation.app_id != app_id:
                    raise BusinessException("会话不属于该应用", BizCode.INVALID_CONVERSATION)
                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "会话不存在，将创建新会话",
                    extra={"conversation_id": str(conversation_id)}
                )

        # 创建新会话（使用发布版本的配置）
        conversation = self.create_conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft
        )

        logger.info(
            "为分享链接创建新会话"
        )

        return conversation

# ==================== 依赖注入函数 ====================

def get_conversation_service(
        db: Annotated[Session, Depends(get_db)]
) -> ConversationService:
    """获取工作流服务（依赖注入）"""
    return ConversationService(db)
