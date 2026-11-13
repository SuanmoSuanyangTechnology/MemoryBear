"""Read-only PostgreSQL repository for working-memory queries.

Mirrors the monolith's read paths for T01–T04 without adding any
workspace or user-ownership validation beyond what the monolith already does.
The boundary script enforces that this file never issues writes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references.memory_statistics_model import (
    Conversation,
    MemoryMessage,
    Message,
)

#: Sources accepted by T03/T04 — same set as the monolith's
#: ``MemoryMessageRepository._API_MCP_SOURCES``.
API_MCP_SOURCES = frozenset({"service_api", "mcp"})

#: Excluded app id used by the monolith's conversation listing query.
#: Mirrors ``conversation_repository._EXCLUDED_APP_ID``.
_EXCLUDED_APP_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class WorkingMemoryRepository:
    """Read-only data access for working-memory endpoints (T01–T04)."""

    def __init__(
        self,
        postgres: PostgresManager,
    ) -> None:
        self.postgres = postgres

    # ── T01: conversations ───────────────────────────────────────────────────

    async def list_conversations_by_end_user(
        self,
        end_user_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated conversation list for one end-user.

        Mirrors ``ConversationRepository.get_conversation_by_user_id_async``
        with ``workspace_id=None`` (no workspace filter) and
        ``is_activate=True``.
        """

        base_filter = [
            Conversation.user_id == end_user_id,
            Conversation.is_active.is_(True),
            Conversation.app_id != _EXCLUDED_APP_ID,
        ]

        async with self.postgres.session() as session:
            count_stmt = select(func.count()).select_from(
                select(Conversation.id).where(*base_filter).subquery()
            )
            total = int((await session.scalar(count_stmt)) or 0)

            rows_stmt = (
                select(Conversation.id, Conversation.title)
                .where(*base_filter)
                .order_by(Conversation.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(rows_stmt)
            items = [
                {"id": str(row.id), "title": row.title}
                for row in result.all()
            ]

        return items, total

    # ── T02: messages ────────────────────────────────────────────────────────

    async def page_messages_by_conversation(
        self,
        conversation_id: str,
        *,
        page: int = 1,
        pagesize: int = 20,
        keyword: str | None = None,
        start_at: datetime | None = None,
        end_at_exclusive: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filtered message page for one conversation.

        Mirrors ``ConversationRepository.get_message_by_conversation_id_async``
        with ``current_only=True``.
        """

        base_filter: list[Any] = [
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_not(True),
            Message.is_current.is_not(False),
        ]
        if keyword is not None:
            base_filter.append(Message.content.icontains(keyword, autoescape=True))
        if start_at is not None:
            base_filter.append(Message.created_at >= start_at)
        if end_at_exclusive is not None:
            base_filter.append(Message.created_at < end_at_exclusive)

        offset = (page - 1) * pagesize

        async with self.postgres.session() as session:
            stmt = (
                select(
                    Message.id,
                    Message.conversation_id,
                    Message.role,
                    Message.content,
                    Message.status,
                    Message.meta_data,
                    Message.created_at,
                    Message.version,
                    Message.is_current,
                    Message.parent_message_id,
                    func.count().over().label("total"),
                )
                .where(*base_filter)
                .order_by(Message.created_at.asc())
                .offset(offset)
                .limit(pagesize)
            )
            rows_result = await session.execute(stmt)
            rows_with_total = rows_result.all()

            if rows_with_total:
                total = int(rows_with_total[0].total)
                items = [self._message_row_to_dict(row) for row in rows_with_total]
            else:
                total_result = await session.execute(
                    select(func.count(Message.id)).where(*base_filter)
                )
                total = int(total_result.scalar_one() or 0)
                items = []

        return items, total
    @staticmethod
    def _message_row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "conversation_id": str(row.conversation_id),
            "role": row.role,
            "content": row.content,
            "status": row.status,
            "meta_data": row.meta_data or {},
            "created_at": row.created_at,
            "version": row.version if row.version is not None else 1,
            "is_current": row.is_current if row.is_current is not None else True,
            "parent_message_id": (
                str(row.parent_message_id) if row.parent_message_id is not None else None
            ),
            # feedback fields are injected by the monolith controller; profile
            # returns null to keep the response shape identical.
            "feedback_type": None,
            "feedback_content": None,
            "is_favorited": None,
        }

    # ── T03: sources ─────────────────────────────────────────────────────────

    async def working_memory_source_stats(
        self,
        end_user_id: str,
    ) -> list[dict[str, Any]]:
        """Per-source message count and latest timestamp.

        Mirrors ``MemoryMessageRepository.get_working_memory_sources_async``.
        """

        async with self.postgres.session() as session:
            stmt = (
                select(
                    MemoryMessage.source,
                    func.count().label("message_count"),
                    func.max(MemoryMessage.created_at).label("latest_at"),
                )
                .where(
                    MemoryMessage.end_user_id == end_user_id,
                    MemoryMessage.conversation_id.is_(None),
                    MemoryMessage.source.in_(API_MCP_SOURCES),
                )
                .group_by(MemoryMessage.source)
            )
            result = await session.execute(stmt)
            return [
                {
                    "source": row.source,
                    "message_count": int(row.message_count),
                    "latest_at": row.latest_at,
                }
                for row in result.all()
            ]

    # ── T04: source_messages ─────────────────────────────────────────────────

    async def page_messages_by_source(
        self,
        end_user_id: str,
        source: str,
        *,
        page: int = 1,
        pagesize: int = 20,
        keyword: str | None = None,
        start_at: datetime | None = None,
        end_at_exclusive: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filtered message page for one API/MCP source.

        Mirrors ``MemoryMessageRepository.list_recent_messages_by_source_async``.
        Ordered by ``created_at ASC`` (oldest first).
        """

        base_filter: list[Any] = [
            MemoryMessage.end_user_id == end_user_id,
            MemoryMessage.conversation_id.is_(None),
            MemoryMessage.source == source,
        ]
        if keyword is not None:
            base_filter.append(MemoryMessage.content.icontains(keyword, autoescape=True))
        if start_at is not None:
            base_filter.append(MemoryMessage.created_at >= start_at)
        if end_at_exclusive is not None:
            base_filter.append(MemoryMessage.created_at < end_at_exclusive)

        offset = (page - 1) * pagesize

        async with self.postgres.session() as session:
            stmt = (
                select(
                    MemoryMessage.role,
                    MemoryMessage.content,
                    MemoryMessage.message_seq,
                    MemoryMessage.created_at,
                    MemoryMessage.dialog_at,
                    func.count().over().label("total"),
                )
                .where(*base_filter)
                .order_by(MemoryMessage.created_at.asc())
                .offset(offset)
                .limit(pagesize)
            )
            rows_result = await session.execute(stmt)
            rows_with_total = rows_result.all()

            if rows_with_total:
                total = int(rows_with_total[0].total)
                items = [
                    {
                        "role": row.role,
                        "content": row.content,
                        "message_seq": row.message_seq,
                        "created_at": row.created_at,
                        "dialog_at": row.dialog_at,
                    }
                    for row in rows_with_total
                ]
            else:
                total_result = await session.execute(
                    select(func.count(MemoryMessage.id)).where(*base_filter)
                )
                total = int(total_result.scalar_one() or 0)
                items = []

        return items, total
