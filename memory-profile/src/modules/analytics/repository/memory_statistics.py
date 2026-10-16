"""Memory statistics queries over API-owned read-only ORM mappings."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    Conversation,
    ForgetAudit,
    MemoryMessage,
    MemoryPerceptual,
    ShortTermMemory,
)

_EXCLUDED_APP_ID = UUID("00000000-0000-0000-0000-000000000001")

#: ``memory_type_counts`` 返回的计数器名字，顺序与 ``_statements`` 一致。
COUNT_KEYS = (
    "perceptual",
    "active_conversations",
    "direct_sources",
    "short_term",
    "pending_conversations",
    "forgotten",
)


class MemoryStatisticsRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    # ------------------------------------------------------------------
    # 各计数器的 SQL。单一来源：合并查询与单指标查询都从这里取语句，
    # 避免两处各写一份 where 条件后悄悄漂移。
    # ------------------------------------------------------------------

    @staticmethod
    def _perceptual_statement(end_user_id: UUID) -> Select:
        return (
            select(func.count())
            .select_from(MemoryPerceptual)
            .where(MemoryPerceptual.end_user_id == end_user_id)
        )

    @staticmethod
    def _active_conversation_statement(end_user_id: UUID) -> Select:
        return (
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.user_id == str(end_user_id),
                Conversation.is_active.is_(True),
                Conversation.app_id != _EXCLUDED_APP_ID,
            )
        )

    @staticmethod
    def _direct_source_statement(end_user_id: UUID) -> Select:
        return select(func.count(func.distinct(MemoryMessage.source))).where(
            MemoryMessage.end_user_id == str(end_user_id),
            MemoryMessage.conversation_id.is_(None),
            MemoryMessage.source.in_(("service_api", "mcp")),
        )

    @staticmethod
    def _short_term_statement(end_user_id: UUID) -> Select:
        return (
            select(func.count())
            .select_from(ShortTermMemory)
            .where(ShortTermMemory.end_user_id == str(end_user_id))
        )

    @staticmethod
    def _pending_conversation_statement(end_user_id: UUID) -> Select:
        return (
            select(func.count(func.distinct(Conversation.id)))
            .select_from(Conversation)
            .join(MemoryMessage, MemoryMessage.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == str(end_user_id),
                MemoryMessage.message_seq > func.coalesce(Conversation.write_cursor, 0),
            )
        )

    @staticmethod
    def _forgotten_statement(end_user_id: UUID) -> Select:
        return (
            select(func.count())
            .select_from(ForgetAudit)
            .where(ForgetAudit.end_user_id == end_user_id)
        )

    @classmethod
    def _statements(cls, end_user_id: UUID) -> tuple[Select, ...]:
        return (
            cls._perceptual_statement(end_user_id),
            cls._active_conversation_statement(end_user_id),
            cls._direct_source_statement(end_user_id),
            cls._short_term_statement(end_user_id),
            cls._pending_conversation_statement(end_user_id),
            cls._forgotten_statement(end_user_id),
        )

    # ------------------------------------------------------------------
    # 合并查询：一次取回全部计数器
    # ------------------------------------------------------------------

    async def memory_type_counts(self, end_user_id: UUID) -> dict[str, int]:
        """一次查询取回全部计数器，返回 ``{COUNT_KEYS: int}``。

        为什么合并而不是并发下发 6 条：这些计数每个的真实 DB 工作都不到 1 ms，
        开销几乎全在「开一个 session + 占一条池连接」上（本机实测每条约 0.9 ms）。
        合并后每请求只占 **1 条连接**，并发上限不再被 6 除；且 6 个数字来自
        **同一个快照**——原先 6 个独立 session 是 6 个独立事务，彼此不保证一致。

        实测（pool_size=10）：6 条并发 6.68 ms → 合并 3.55 ms，数值完全一致。
        """
        statement = select(
            *(
                sub.scalar_subquery().label(key)
                for key, sub in zip(COUNT_KEYS, self._statements(end_user_id), strict=True)
            )
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one()
        return {key: int(row[key] or 0) for key in COUNT_KEYS}

    # ------------------------------------------------------------------
    # 单指标查询：合并查询失败时的回落路径（保留「单个指标失败只降级为 0」语义）
    # ------------------------------------------------------------------

    async def perceptual_count(self, end_user_id: UUID) -> int:
        return await self._count(self._perceptual_statement(end_user_id))

    async def active_conversation_count(self, end_user_id: UUID) -> int:
        return await self._count(self._active_conversation_statement(end_user_id))

    async def direct_source_count(self, end_user_id: UUID) -> int:
        return await self._count(self._direct_source_statement(end_user_id))

    async def short_term_count(self, end_user_id: UUID) -> int:
        return await self._count(self._short_term_statement(end_user_id))

    async def pending_conversation_count(self, end_user_id: UUID) -> int:
        return await self._count(self._pending_conversation_statement(end_user_id))

    async def forgotten_count(self, end_user_id: UUID) -> int:
        return await self._count(self._forgotten_statement(end_user_id))

    async def _count(self, statement: Select) -> int:
        async with self.postgres.session() as session:
            value = await session.scalar(statement)
        return int(value or 0)
