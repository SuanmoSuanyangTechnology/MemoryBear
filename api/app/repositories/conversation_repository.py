import uuid
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, desc, func

from app.core.exceptions import ResourceNotFoundException
from app.core.logging_config import get_db_logger
from app.models import Conversation, Message

logger = get_db_logger()


class ConversationRepository:
    """Repository for Conversation entity, encapsulating CRUD operations."""

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
        """
        Create a new conversation record.

        Args:
            app_id: Application ID the conversation belongs to.
            workspace_id: Workspace ID where the conversation is created.
            user_id: Optional user ID associated with the conversation.
            title: Optional conversation title. Defaults to "New Conversation".
            is_draft: Whether the conversation is a draft.
            config_snapshot: Optional configuration snapshot.

        Returns:
            Conversation: Newly created Conversation instance.
        """
        conversation = Conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            title=title or "New Conversation",
            is_draft=is_draft,
            config_snapshot=config_snapshot
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        logger.info(
            "Create Conversation Success",
            extra={
                "conversation_id": str(conversation.id),
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "is_draft": is_draft
            }
        )
        return conversation

    def get_conversation_by_conversation_id(
            self,
            conversation_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> Conversation:
        """
        Retrieve a conversation by its ID, optionally filtered by workspace.

        Args:
            conversation_id: The UUID of the conversation.
            workspace_id: Optional workspace UUID to filter the conversation.

        Raises:
            ResourceNotFoundException: If conversation does not exist.

        Returns:
            Conversation: The matching Conversation instance.
        """
        logger.info(f"Fetching conversation: {conversation_id}")

        stmt = select(Conversation).where(Conversation.id == conversation_id)

        if workspace_id:
            stmt = stmt.where(Conversation.workspace_id == workspace_id)

        conversation = self.db.scalars(stmt).first()

        if not conversation:
            logger.warning(f"Conversation not found: {conversation_id}")
            raise ResourceNotFoundException("Conversation", str(conversation_id))

        logger.info(f"Conversation fetched successfully: {conversation_id}")
        return conversation

    def list_conversations(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: Optional[str] = None,
            is_draft: Optional[bool] = None,
            page: int = 1,
            pagesize: int = 20
    ) -> tuple[list[Conversation], int]:
        """
        List conversations with optional filters and pagination.

        Args:
            app_id: Application ID filter.
            workspace_id: Workspace ID filter.
            user_id: Optional user ID filter.
            is_draft: Optional draft status filter.
            page: Page number (1-based).
            pagesize: Number of items per page.

        Returns:
            Tuple[List[Conversation], int]: List of Conversation instances and total count.
        """
        stmt = select(Conversation).where(
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.is_active.is_(True)
        )

        if user_id:
            stmt = stmt.where(Conversation.user_id == user_id)

        if is_draft is not None:
            stmt = stmt.where(Conversation.is_draft == is_draft)

        # Calculate total number of records
        total = int(self.db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one())

        # Apply pagination
        stmt = stmt.order_by(desc(Conversation.updated_at))
        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)

        conversations = list(self.db.scalars(stmt).all())

        logger.info(
            "Listed conversations successfully",
            extra={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "returned": len(conversations),
                "total": total
            }
        )
        return conversations, total

    def soft_delete_conversation_by_conversation_id(
            self,
            conversation_id: uuid.UUID,
            workspace_id: uuid.UUID,
    ):
        """
        Soft delete a conversation by setting is_active to False.

        Args:
            conversation_id: The UUID of the conversation.
            workspace_id: Workspace ID for verification.
        """
        conversation = self.get_conversation_by_conversation_id(
            conversation_id,
            workspace_id
        )
        conversation.is_active = False

        self.db.commit()

        logger.info(
            "Soft deleted conversation successfully",
            extra={
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id)
            }
        )


class MessageRepository:
    """Repository for Message entity, encapsulating CRUD operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_message_by_conversation_id(
            self,
            conversation_id: uuid.UUID,
            limit: Optional[int] = None
    ) -> list[Message]:
        """
        Retrieve messages by conversation ID.

        Args:
            conversation_id: The UUID of the conversation.
            limit: Optional limit on the number of messages returned.

        Returns:
            List[Message]: List of Message instances.
        """
        stmt = select(Message).where(
            Message.conversation_id == conversation_id
        ).order_by(Message.created_at)

        if limit:
            stmt = stmt.limit(limit)

        messages = list(self.db.scalars(stmt).all())

        logger.info(
            "Fetched messages successfully",
            extra={
                "conversation_id": str(conversation_id),
                "returned": len(messages)
            }
        )
        return messages


class UnitOfWork:
    """Unit of Work pattern to manage transactions across Conversation and Message."""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repo = ConversationRepository(db)
        self.message_repo = MessageRepository(db)

    def add_message(
            self,
            conversation_id: uuid.UUID,
            role: str,
            content: str,
            meta_data: Optional[dict] = None
    ) -> Message:
        """
        Add a message to a conversation, updating conversation counters and title.

        Args:
            conversation_id: Conversation UUID.
            role: Message role, e.g., 'user' or 'assistant'.
            content: Message content text.
            meta_data: Optional metadata associated with the message.

        Returns:
            Message: Newly created Message instance.
        """
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            meta_data=meta_data
        )

        self.db.add(message)

        # Update conversation metadata
        conversation = self.conversation_repo.get_conversation_by_conversation_id(conversation_id)
        conversation.message_count += 1

        # Set title from first user message
        if conversation.message_count == 1 and role == "user":
            conversation.title = content[:50] + ("..." if len(content) > 50 else "")

        self.db.commit()
        self.db.refresh(message)

        logger.info(
            "Message added successfully",
            extra={
                "conversation_id": str(conversation_id),
                "message_id": str(message.id),
                "role": role,
                "content_length": len(content)
            }
        )
        return message
