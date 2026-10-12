"""Memory statistics queries over API-owned read-only ORM mappings."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    Conversation,
    ForgetAudit,
    MemoryMessage,
    MemoryPerceptual,
    ShortTermMemory,
)

_EXCLUDED_APP_ID = UUID("00000000-0000-0000-0000-000000000001")


class MemoryStatisticsRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def perceptual_count(self, end_user_id: UUID) -> int:
        statement = (
            select(func.count())
            .select_from(MemoryPerceptual)
            .where(MemoryPerceptual.end_user_id == end_user_id)
        )
        return await self._count(statement)

    async def active_conversation_count(self, end_user_id: UUID) -> int:
        statement = (
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.user_id == str(end_user_id),
                Conversation.is_active.is_(True),
                Conversation.app_id != _EXCLUDED_APP_ID,
            )
        )
        return await self._count(statement)

    async def direct_source_count(self, end_user_id: UUID) -> int:
        statement = select(func.count(func.distinct(MemoryMessage.source))).where(
            MemoryMessage.end_user_id == str(end_user_id),
            MemoryMessage.conversation_id.is_(None),
            MemoryMessage.source.in_(("service_api", "mcp")),
        )
        return await self._count(statement)

    async def short_term_count(self, end_user_id: UUID) -> int:
        statement = (
            select(func.count())
            .select_from(ShortTermMemory)
            .where(ShortTermMemory.end_user_id == str(end_user_id))
        )
        return await self._count(statement)

    async def pending_conversation_count(self, end_user_id: UUID) -> int:
        statement = (
            select(func.count(func.distinct(Conversation.id)))
            .select_from(Conversation)
            .join(MemoryMessage, MemoryMessage.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == str(end_user_id),
                MemoryMessage.message_seq > func.coalesce(Conversation.write_cursor, 0),
            )
        )
        return await self._count(statement)

    async def forgotten_count(self, end_user_id: UUID) -> int:
        statement = (
            select(func.count())
            .select_from(ForgetAudit)
            .where(ForgetAudit.end_user_id == end_user_id)
        )
        return await self._count(statement)

    async def _count(self, statement) -> int:
        async with self.postgres.session() as session:
            value = await session.scalar(statement)
        return int(value or 0)
