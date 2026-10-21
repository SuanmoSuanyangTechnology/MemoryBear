"""Conversation/message read paths used by the application-log and public-share
endpoints (frozen port of the monolith ``conversation_repository`` read subset)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import ResourceNotFoundException
from ..models.conversation_model import Conversation, Message

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Conversation table reads (app-log / share faces) plus the chat-kernel writes."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def create_conversation(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str | None = None,
        title: str | None = None,
        is_draft: bool = False,
        config_snapshot: dict | None = None,
    ) -> Conversation:
        """Stage a new conversation row; the caller owns the commit."""

        conversation = Conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            title=title or "New Conversation",
            is_draft=is_draft,
            config_snapshot=config_snapshot,
        )
        self.db.add(conversation)
        return conversation

    def list_app_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        is_draft: bool | None = None,
        keyword: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[Conversation], int]:
        """Filtered + paginated app-log conversation list (newest first);
        returns (rows, total)."""

        base_conditions = [
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.is_active.is_(True),
        ]
        if is_draft is not None:
            base_conditions.append(Conversation.is_draft == is_draft)

        # Time-range filters ride the ix_app_call_logs_app_id_created_at index.
        if start_date:
            base_conditions.append(Conversation.created_at >= start_date)
        if end_date:
            base_conditions.append(Conversation.created_at <= end_date)

        base_stmt = select(Conversation).where(*base_conditions)

        if keyword:
            kw_pattern = f"%{keyword}%"
            # EXISTS instead of IN: stops at the first matching message.
            base_stmt = base_stmt.where(
                select(Message.conversation_id)
                .where(
                    Message.conversation_id == Conversation.id,
                    Message.content.ilike(kw_pattern),
                )
                .exists()
            )

        total = int(self.db.execute(
            select(func.count()).select_from(base_stmt.subquery())
        ).scalar_one())

        stmt = base_stmt.order_by(desc(Conversation.updated_at))
        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)

        conversations = list(self.db.scalars(stmt).all())

        logger.info(
            "Listed app conversations successfully",
            extra={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "keyword": keyword,
                "returned": len(conversations),
                "total": total,
            },
        )
        return conversations, total

    def get_conversation_for_app_log(
        self,
        conversation_id: uuid.UUID,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> Conversation:
        """One active conversation of the app + workspace, else not-found."""

        logger.info("Fetching conversation for app log: %s", conversation_id)

        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.is_active.is_(True),
        )

        conversation = self.db.scalars(stmt).first()

        if not conversation:
            logger.warning("Conversation not found: %s", conversation_id)
            raise ResourceNotFoundException("会话", str(conversation_id))

        logger.info("Conversation fetched successfully: %s", conversation_id)
        return conversation

    async def get_conversation_by_conversation_id_async(
        self,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
    ) -> Conversation:
        """Async variant: one conversation by id (optionally workspace-scoped),
        else not-found."""

        stmt = select(Conversation).where(Conversation.id == conversation_id)
        if workspace_id:
            stmt = stmt.where(Conversation.workspace_id == workspace_id)

        conversation = (await self.db.execute(stmt)).scalar_one_or_none()

        if not conversation:
            logger.warning("Conversation not found: %s", conversation_id)
            raise ResourceNotFoundException("会话", str(conversation_id))

        return conversation

    def get_conversation_by_conversation_id(
        self,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
    ) -> Conversation:
        """One conversation by id (optionally scoped to a workspace), else not-found."""

        stmt = select(Conversation).where(Conversation.id == conversation_id)
        if workspace_id:
            stmt = stmt.where(Conversation.workspace_id == workspace_id)

        conversation = self.db.scalars(stmt).first()

        if not conversation:
            logger.warning("Conversation not found: %s", conversation_id)
            raise ResourceNotFoundException("会话", str(conversation_id))

        return conversation

    def list_v1_conversations(
        self,
        *,
        app_id: uuid.UUID | None,
        workspace_id: uuid.UUID,
        internal_user_id: str,
        page: int,
        page_size: int,
    ) -> tuple[list[Conversation], int]:
        """Paginated v1 conversation list of one external end-user (newest first);
        returns (rows, total)."""

        base_stmt = select(Conversation).where(
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == internal_user_id,
            Conversation.is_active.is_(True),
            Conversation.is_draft.is_(False),
        )

        total = int(self.db.execute(
            select(func.count()).select_from(base_stmt.subquery())
        ).scalar_one())

        stmt = base_stmt.order_by(desc(Conversation.updated_at))
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        conversations = list(self.db.scalars(stmt).all())
        return conversations, total

    def list_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str | None = None,
        is_draft: bool | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[Conversation], int]:
        """Filtered + paginated conversation list (newest first); returns
        (rows, total)."""

        stmt = select(Conversation).where(
            Conversation.app_id == app_id,
            Conversation.workspace_id == workspace_id,
            Conversation.is_active.is_(True),
        )

        if user_id:
            stmt = stmt.where(Conversation.user_id == str(user_id))

        if is_draft is not None:
            stmt = stmt.where(Conversation.is_draft == is_draft)

        total = int(self.db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one())

        stmt = stmt.order_by(desc(Conversation.updated_at))
        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)

        conversations = list(self.db.scalars(stmt).all())

        logger.info(
            "Listed conversations successfully",
            extra={
                "app_id": str(app_id),
                "workspace_id": str(workspace_id),
                "returned": len(conversations),
                "total": total,
            },
        )
        return conversations, total


class MessageRepository:
    """Message table reads (app-log face) plus the chat-kernel writes."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def add_message(self, message: Message) -> Message:
        """Stage a new message row; the caller owns the commit/rollback."""

        self.db.add(message)
        return message

    async def get_message_by_conversation_id_async(
        self,
        conversation_id: uuid.UUID,
        limit: int | None = None,
        current_only: bool = True,
        offset: int | None = None,
        keyword: str | None = None,
        start_at: datetime | None = None,
        end_at_exclusive: datetime | None = None,
    ) -> tuple[list[Message], int]:
        """Async filtered message read.

        Returns messages ordered by creation time plus the filtered total.
        The total rides a window count on the same query; an empty page falls
        back to a dedicated count so paging totals stay correct.
        """

        base_filter = [
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_not(True),
        ]
        if current_only:
            base_filter.append(Message.is_current.is_not(False))
        if keyword is not None:
            base_filter.append(Message.content.icontains(keyword, autoescape=True))
        if start_at is not None:
            base_filter.append(Message.created_at >= start_at)
        if end_at_exclusive is not None:
            base_filter.append(Message.created_at < end_at_exclusive)

        stmt = (
            select(
                Message,
                func.count().over().label("total"),
            )
            .where(*base_filter)
            .order_by(Message.created_at.asc())
        )
        if offset is not None:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)

        rows_with_total = (await self.db.execute(stmt)).all()
        if rows_with_total:
            total = int(rows_with_total[0].total)
            messages = [row[0] for row in rows_with_total]
        else:
            total_result = await self.db.execute(
                select(func.count(Message.id)).where(*base_filter)
            )
            total = int(total_result.scalar_one())
            messages = []

        return messages, total

    def get_messages_by_conversation(
        self,
        conversation_id: uuid.UUID,
        current_only: bool = True,
    ) -> list[Message]:
        """All messages of a conversation in ascending time order."""

        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_not(True),
        )

        if current_only:
            stmt = stmt.where(Message.is_current.is_not(False))

        stmt = stmt.order_by(Message.created_at)

        messages = list(self.db.scalars(stmt).all())

        logger.info(
            "Fetched messages for conversation",
            extra={
                "conversation_id": str(conversation_id),
                "message_count": len(messages),
            },
        )
        return messages

    def get_message_by_conversation_id(
        self,
        conversation_id: uuid.UUID,
        limit: int | None = None,
        current_only: bool = True,
    ) -> list[Message]:
        """Messages of a conversation in ascending time order (optionally
        limited to current versions and to the newest ``limit`` rows)."""

        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_not(True),
        )

        if current_only:
            stmt = stmt.where(Message.is_current.is_not(False))

        stmt = stmt.order_by(Message.created_at)
        if limit:
            stmt = stmt.limit(limit)

        return list(self.db.scalars(stmt).all())


__all__ = ["ConversationRepository", "MessageRepository"]
