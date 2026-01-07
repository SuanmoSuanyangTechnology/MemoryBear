"""会话服务"""
import uuid
from typing import Annotated
from typing import Optional, List, Tuple

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.exceptions import ResourceNotFoundException
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.models import Conversation, Message
from app.repositories.conversation_repository import ConversationRepository, UnitOfWork, MessageRepository

logger = get_business_logger()


class ConversationService:
    """
    Service layer for managing conversations and messages.
    Provides methods to create, retrieve, list, and manipulate conversations and messages.
    Delegates database operations to repositories.
    """

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repo = ConversationRepository(db)
        self.message_repo = MessageRepository(db)
        self.unit_repo_option = UnitOfWork(db)

    def create_conversation(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: Optional[str] = None,
            title: Optional[str] = None,
            is_draft: bool = False,
            config_snapshot: Optional[dict] = None
    ) -> Conversation:
        """
        Create a new conversation in the system.

        Args:
            app_id (uuid.UUID): The application ID the conversation belongs to.
            workspace_id (uuid.UUID): Workspace ID for context.
            user_id (Optional[str]): Optional user ID for the conversation owner.
            title (Optional[str]): Conversation title. Defaults to 'New Conversation' if not provided.
            is_draft (bool): Whether the conversation is a draft.
            config_snapshot (Optional[dict]): Optional configuration snapshot.

        Returns:
            Conversation: Newly created Conversation instance.
        """
        conversation = self.conversation_repo.create_conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            title=title or "New Conversation",
            is_draft=is_draft,
            config_snapshot=config_snapshot
        )

        return conversation

    def get_conversation(
            self,
            conversation_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> Conversation:
        """
        Retrieve a conversation by its ID.

        Args:
            conversation_id (uuid.UUID): The conversation UUID.
            workspace_id (Optional[uuid.UUID]): Optional workspace UUID to restrict the query.

        Raises:
            ResourceNotFoundException: If the conversation does not exist.

        Returns:
            Conversation: The requested Conversation instance.
        """
        conversation = self.conversation_repo.get_conversation_by_conversation_id(
            conversation_id=conversation_id,
            workspace_id=workspace_id
        )

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
        """
        List conversations with optional filters and pagination.

        Args:
            app_id (uuid.UUID): Application ID filter.
            workspace_id (uuid.UUID): Workspace ID filter.
            user_id (Optional[str]): Optional user ID filter.
            is_draft (Optional[bool]): Optional draft status filter.
            page (int): Page number, 1-based.
            pagesize (int): Number of items per page.

        Returns:
            Tuple[List[Conversation], int]: A list of Conversation instances and the total count.
        """
        conversations, total = self.conversation_repo.list_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft,
            page=page,
            pagesize=pagesize
        )

        return conversations, total

    def add_message(
            self,
            conversation_id: uuid.UUID,
            role: str,
            content: str,
            meta_data: Optional[dict] = None
    ) -> Message:
        """
        Add a message to a conversation using UnitOfWork.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            role (str): Role of the message sender ('user' or 'assistant').
            content (str): Message content.
            meta_data (Optional[dict]): Optional metadata.

        Returns:
            Message: Newly created Message instance.
        """
        message = self.unit_repo_option.add_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            meta_data=meta_data
        )

        return message

    def get_messages(
            self,
            conversation_id: uuid.UUID,
            limit: Optional[int] = None
    ) -> List[Message]:
        """
        Retrieve messages for a conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            limit (Optional[int]): Optional maximum number of messages.

        Returns:
            List[Message]: List of messages ordered by creation time.
        """
        messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit
        )

        return messages

    def get_conversation_history(
            self,
            conversation_id: uuid.UUID,
            max_history: Optional[int] = None
    ) -> List[dict]:
        """
        Retrieve historical conversation messages formatted as dictionaries.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            max_history (Optional[int]): Maximum number of messages to retrieve.

        Returns:
            List[dict]: List of message dictionaries with keys 'role' and 'content'.
        """
        messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit=max_history
        )

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
        """
        Save a pair of user and assistant messages to the conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            user_message (str): User's message content.
            assistant_message (str): Assistant's response content.
        """
        self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=user_message
        )

        self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_message
        )

        logger.debug(
            "Saved conversation messages successfully",
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
        """
        Soft delete a conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            workspace_id (uuid.UUID): Workspace UUID for validation.
        """
        self.conversation_repo.soft_delete_conversation_by_conversation_id(
            conversation_id,
            workspace_id
        )

    def create_or_get_conversation(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            is_draft: bool = False,
            conversation_id: Optional[uuid.UUID] = None,
            user_id: Optional[str] = None,
    ) -> Conversation:
        """
        Retrieve an existing conversation by ID or create a new one.

        Args:
            app_id (uuid.UUID): Application ID.
            workspace_id (uuid.UUID): Workspace ID.
            is_draft (bool): Whether the conversation should be a draft.
            conversation_id (Optional[uuid.UUID]): Optional conversation ID to retrieve.
            user_id (Optional[str]): Optional user ID.

        Returns:
            Conversation: Existing or newly created conversation.
        """
        if conversation_id:
            try:
                conversation = self.get_conversation(
                    conversation_id=conversation_id,
                    workspace_id=workspace_id
                )

                # 验证会话是否属于该应用
                if conversation.app_id != app_id:
                    raise BusinessException(
                        "Conversation does not belong to this app",
                        BizCode.INVALID_CONVERSATION
                    )
                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "Conversation not found. A new conversation will be created.",
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
            "Created a new conversation for shared link usage",
            extra={
                "conversation_id": str(conversation_id),
            }
        )

        return conversation


# ==================== Dependency Injection ====================

def get_conversation_service(
        db: Annotated[Session, Depends(get_db)]
) -> ConversationService:
    """
    Dependency injection function to provide ConversationService instance.

    Args:
        db (Session): Database session provided by FastAPI dependency.

    Returns:
        ConversationService: Service instance.
    """
    return ConversationService(db)
