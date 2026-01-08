import uuid
from typing import Optional

from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundException
from app.core.logging_config import get_db_logger
from app.models import Conversation, Message
from app.models.conversation_model import ConversationDetail

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

    def get_conversation_by_user_id(
            self,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID = None,
            limit: int = 10,
            is_activate: bool = True
    ) -> list[Conversation]:
        """
        Retrieve recent conversations for a specific user.

        This method queries conversations associated with the given user ID,
        optionally scoped to a specific workspace. Results are ordered by the
        most recently updated conversations and limited to a fixed number.

        Args:
            user_id (uuid.UUID): Unique identifier of the user.
            workspace_id (uuid.UUID, optional): Workspace scope for the query.
                If provided, only conversations under this workspace will be returned.
            limit (int): Maximum number of conversations to return.
                Defaults to 10.
            is_activate (bool): Convsersation State limit

        Returns:
            list[Conversation]: A list of conversation entities ordered by
            last updated time (descending).
        """
        logger.info(f"Fetching conversation by user_id: {user_id}")

        stmt = select(Conversation).where(
            Conversation.user_id == str(user_id),
            Conversation.is_active.is_(is_activate)
        )

        if workspace_id:
            stmt = stmt.where(Conversation.workspace_id == workspace_id)

        stmt = stmt.order_by(desc(Conversation.updated_at))
        stmt = stmt.limit(limit)

        convsersations = list(self.db.scalars(stmt).all())
        logger.info(
            "Conversation fetched successfully",
            extra={
                "user_id": str(user_id),
                "workspace_id": str(workspace_id),
            }
        )
        return convsersations

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
            stmt = stmt.where(Conversation.user_id == str(user_id))

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

    def get_conversation_detail(
            self,
            conversation_id: uuid.UUID
    ) -> ConversationDetail | None:
        """
    Retrieve the detail of a conversation by its ID.

    Args:
        conversation_id (UUID): The unique identifier of the conversation.

    Returns:
        ConversationDetail or None: The conversation detail object if found,
        otherwise None.

    Notes:
        - This method queries the database but does not modify it.
        - The caller is responsible for handling the case where None is returned.
    """
        stmt = select(ConversationDetail).where(
            ConversationDetail.conversation_id == conversation_id
        )
        detail = self.db.scalars(stmt).first()
        return detail

    def add_conversation_detail(
            self,
            conversation_detail: ConversationDetail,
    ):
        """
        Add a new conversation detail record to the database session.

        Args:
            conversation_detail (ConversationDetail): The ORM object representing
                the conversation detail to add.

        Returns:
            ConversationDetail: The same object added to the session.

        Notes:
            - This method only adds the object to the current session.
            - It does not commit the transaction; commit/rollback is handled
              by the caller.
            - Useful for batch operations or transactional control.
        """
        self.db.add(conversation_detail)
        return conversation_detail


class MessageRepository:
    """Repository for Message entity, encapsulating CRUD operations."""

    def __init__(self, db: Session):
        self.db = db

    def add_message(self, message: Message) -> Message:
        """
        Add a new message record to the conversation.

        Args:
            message (Message): The Message ORM object to be added.

        Returns:
            Message: The same message object added to the conversation.

        Notes:
            - This method only adds the object to the current conversation.
            - It does not commit the transaction; commit/rollback should be handled
              by the caller.
            - Useful for transactional control or batch operations.
        """
        self.db.add(message)
        return message

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
