"""Memory statistics queries over API-owned read-only ORM mappings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, any_, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    Conversation,
    EndUser,
    ForgetAudit,
    MemoryMessage,
    MemoryPerceptual,
)
from src.modules.analytics.constants import COUNT_KEYS

_EXCLUDED_APP_ID = UUID("00000000-0000-0000-0000-000000000001")


class MemoryStatisticsRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    # ------------------------------------------------------------------
    # 各计数器的 SQL。单一来源：合并查询与单指标查询都从这里取语句，
    # 避免两处各写一份 where 条件后悄悄漂移。
    #
    # 每个 builder 只接收「用户范围」谓词：单用户传等值，工作空间批量传
    # ``= ANY($ids)``。其余业务条件留在 builder 内，因此两条路径的口径不会分叉。
    # ------------------------------------------------------------------

    @staticmethod
    def _perceptual_statement(user_filter: ColumnElement[bool]) -> Select:
        return (
            select(func.count())
            .select_from(MemoryPerceptual)
            .where(user_filter)
        )

    @staticmethod
    def _active_conversation_statement(user_filter: ColumnElement[bool]) -> Select:
        return (
            select(func.count())
            .select_from(Conversation)
            .where(
                user_filter,
                Conversation.is_active.is_(True),
                Conversation.app_id != _EXCLUDED_APP_ID,
            )
        )

    @staticmethod
    def _direct_source_statement(user_filter: ColumnElement[bool]) -> Select:
        # 按 (用户, 来源) 分组后计数：单用户时等价于 count(distinct source)，
        # 批量时得到「用户-source 组合数」，与旧实现的批量口径一致。
        grouped_sources = (
            select(MemoryMessage.end_user_id, MemoryMessage.source)
            .where(
                user_filter,
                MemoryMessage.conversation_id.is_(None),
                MemoryMessage.source.in_(("service_api", "mcp")),
            )
            .group_by(MemoryMessage.end_user_id, MemoryMessage.source)
            .subquery()
        )
        return select(func.count()).select_from(grouped_sources)

    @staticmethod
    def _forgotten_statement(user_filter: ColumnElement[bool]) -> Select:
        return (
            select(func.count())
            .select_from(ForgetAudit)
            .where(user_filter)
        )

    @classmethod
    def _statements(cls, end_user_id: UUID) -> tuple[Select, ...]:
        return (
            cls._perceptual_statement(MemoryPerceptual.end_user_id == end_user_id),
            cls._active_conversation_statement(Conversation.user_id == str(end_user_id)),
            cls._direct_source_statement(MemoryMessage.end_user_id == str(end_user_id)),
            cls._forgotten_statement(ForgetAudit.end_user_id == end_user_id),
        )

    @classmethod
    def _batch_statements(cls, uuid_ids: Any, str_ids: Any) -> tuple[Select, ...]:
        return (
            cls._perceptual_statement(MemoryPerceptual.end_user_id == any_(uuid_ids)),
            cls._active_conversation_statement(Conversation.user_id == any_(str_ids)),
            cls._direct_source_statement(MemoryMessage.end_user_id == any_(str_ids)),
            cls._forgotten_statement(ForgetAudit.end_user_id == any_(uuid_ids)),
        )

    # ------------------------------------------------------------------
    # 单用户合并查询：一次取回全部计数器
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
    # 工作空间批量查询
    # ------------------------------------------------------------------

    async def active_end_user_ids_page(
        self,
        workspace_id: UUID,
        after_id: UUID | None,
        limit: int,
    ) -> list[UUID]:
        """按 ID 游标分页查询 workspace 下的活跃终端用户 ID。

        以 ``id`` 升序作为游标，保证同一轮快照计算不会因并发新增/删除用户而漏算或重算。
        """
        statement = select(EndUser.id).where(
            EndUser.workspace_id == workspace_id,
            EndUser.is_active.is_(True),
        )
        if after_id is not None:
            statement = statement.where(EndUser.id > after_id)
        statement = statement.order_by(EndUser.id.asc()).limit(limit)
        async with self.postgres.session() as session:
            values = (await session.scalars(statement)).all()
        return [UUID(str(value)) for value in values]

    async def counts_for_users(self, end_user_ids: list[UUID]) -> dict[str, int]:
        """Read the four PG counters for one end-user batch (``{COUNT_KEYS: int}``).

        与 :meth:`memory_type_counts` 同款取舍：各计数来自同一快照、只占 1 条连接。
        """
        if not end_user_ids:
            return dict.fromkeys(COUNT_KEYS, 0)
        uuid_ids = bindparam(
            "workspace_statistics_uuid_ids",
            value=end_user_ids,
            type_=ARRAY(PGUUID(as_uuid=True)),
        )
        str_ids = bindparam(
            "workspace_statistics_str_ids",
            value=[str(end_user_id) for end_user_id in end_user_ids],
            type_=ARRAY(MemoryMessage.end_user_id.type),
        )
        statement = select(
            *(
                sub.scalar_subquery().label(key)
                for key, sub in zip(
                    COUNT_KEYS,
                    self._batch_statements(uuid_ids, str_ids),
                    strict=True,
                )
            )
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one()
        return {key: int(row[key] or 0) for key in COUNT_KEYS}

    # ------------------------------------------------------------------
    # 单指标查询：合并查询失败时的回落路径（保留「单个指标失败只降级为 0」语义）
    # ------------------------------------------------------------------

    async def perceptual_count(self, end_user_id: UUID) -> int:
        return await self._count(
            self._perceptual_statement(MemoryPerceptual.end_user_id == end_user_id)
        )

    async def active_conversation_count(self, end_user_id: UUID) -> int:
        return await self._count(
            self._active_conversation_statement(Conversation.user_id == str(end_user_id))
        )

    async def direct_source_count(self, end_user_id: UUID) -> int:
        return await self._count(
            self._direct_source_statement(MemoryMessage.end_user_id == str(end_user_id))
        )

    async def forgotten_count(self, end_user_id: UUID) -> int:
        return await self._count(
            self._forgotten_statement(ForgetAudit.end_user_id == end_user_id)
        )

    async def _count(self, statement: Select) -> int:
        async with self.postgres.session() as session:
            value = await session.scalar(statement)
        return int(value or 0)
