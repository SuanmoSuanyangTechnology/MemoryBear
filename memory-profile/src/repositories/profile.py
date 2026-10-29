"""Read-only end-user profile and memory-statistics queries.

The PostgreSQL reads run over API-owned mappings (insight/summary columns,
memory counters, workspace hot tags); :class:`UserProfileRepository` is the
Neo4j read behind the legacy admin ``user_profile`` endpoint.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import ColumnElement, Select, any_, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from src.common.vocabulary import COUNT_KEYS
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.models.references import (
    Conversation,
    EndUser,
    ForgetAudit,
    ImplicitEmotionsStorage,
    MemoryMessage,
    MemoryPerceptual,
)
from src.repositories.end_user import active_users_in_workspace, scoped_end_user_select

#: 洞察用例需要的列（api ``get_memory_insight_by_end_user_id_async`` 同款子集）。
_MEMORY_INSIGHT_COLUMNS = (
    EndUser.memory_insight,
    EndUser.behavior_pattern,
    EndUser.key_findings,
    EndUser.growth_trajectory,
    EndUser.memory_insight_updated_at,
)

#: 摘要用例需要的列（api ``get_user_summary_by_end_user_id_async`` 同款子集）。
_USER_SUMMARY_COLUMNS = (
    EndUser.user_summary,
    EndUser.personality_traits,
    EndUser.core_values,
    EndUser.one_sentence_summary,
    EndUser.user_summary_updated_at,
    EndUser.memory_tags,
)


class EndUserProfileRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def get_memory_insight(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """洞察用例的窄读：只取 :data:`_MEMORY_INSIGHT_COLUMNS`，不捎带摘要列。"""

        statement = scoped_end_user_select(
            *_MEMORY_INSIGHT_COLUMNS, end_user_id=end_user_id, workspace_id=workspace_id
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row else None

    async def get_user_summary(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """摘要用例的窄读：只取 :data:`_USER_SUMMARY_COLUMNS`，不捎带洞察列。"""

        statement = scoped_end_user_select(
            *_USER_SUMMARY_COLUMNS, end_user_id=end_user_id, workspace_id=workspace_id
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row else None


#: ``MemoryMessage`` 计数里排除的应用 ID。
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
            *active_users_in_workspace(workspace_id)
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

    async def habit_count_for_users(self, end_user_ids: list[str]) -> int:
        """Count cached habits for one user or a workspace user batch."""
        if not end_user_ids:
            return 0
        statement = select(ImplicitEmotionsStorage.implicit_profile).where(
            ImplicitEmotionsStorage.end_user_id.in_(end_user_ids)
        )
        async with self.postgres.session() as session:
            profiles = (await session.scalars(statement)).all()
        count = 0
        for profile in profiles:
            if isinstance(profile, dict):
                habits = profile.get("habits", [])
                if isinstance(habits, list):
                    count += len(habits)
        return count


class WorkspaceProfileRepository:
    """只读的工作空间画像聚合（与 api ``EndUserRepository`` 同款 SQL）。"""

    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def hot_memory_tags(
        self,
        workspace_id: UUID,
        limit: int,
        max_tag_length: int,
    ) -> list[tuple[str, int]]:
        """聚合工作空间活跃终端用户名片 Tag 的 Top-N。

        规范化（空白折叠 / 去空 / 长度上限）与大小写合并整体下沉到 PostgreSQL，
        与 api ``get_hot_memory_tags_by_workspace_async`` 保持同一套 SQL 语义：
        代表文本取同一折叠键下码点序最小值，``frequency`` 为采用该 Tag 的用户数。
        """

        expanded_tag = (
            func.jsonb_array_elements_text(
                sa.case(
                    (
                        func.jsonb_typeof(EndUser.memory_tags) == "array",
                        EndUser.memory_tags,
                    ),
                    else_=func.jsonb_build_array(),
                )
            )
            .table_valued(sa.column("value", sa.Text))
            .lateral()
        )
        tag = func.btrim(
            func.regexp_replace(expanded_tag.c.value, "[[:space:]]+", " ", "g")
        )
        per_user_tags = (
            select(
                EndUser.id.label("end_user_id"),
                tag.label("tag"),
                func.lower(tag).label("tag_key"),
            )
            .select_from(EndUser)
            .join(expanded_tag, sa.true())
            .where(
                *active_users_in_workspace(workspace_id),
                tag != "",
                func.char_length(tag) <= max_tag_length,
            )
            .distinct()
            .cte("per_user_tags")
        )
        hot_tag_name = func.min(sa.collate(per_user_tags.c.tag, "C"))
        user_count = func.count(sa.distinct(per_user_tags.c.end_user_id))
        statement = (
            select(
                hot_tag_name.label("name"),
                user_count.label("frequency"),
            )
            .group_by(per_user_tags.c.tag_key)
            .order_by(user_count.desc(), hot_tag_name.asc())
            .limit(limit)
        )
        async with self.postgres.session() as session:
            result = await session.execute(statement)
            return [(name, int(frequency)) for name, frequency in result.all()]


#: api ``get_user_profile`` 内固定的排除名单（AI/示例用户/占位名）。
HOT_TAG_EXCLUDED_NAMES = [
    "AI",
    "Caroline",
    "Melanie",
    "Jon",
    "Gina",
    "用户",
    "AI助手",
    "John",
    "Maria",
]


class UserProfileRepository:
    """终端用户图谱画像的只读查询（legacy 管理端 ``user_profile``）。"""

    def __init__(self, neo4j: Neo4jManager) -> None:
        self.neo4j = neo4j

    async def top_entities(
        self,
        end_user_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """按出现次数取用户高频实体（api ``get_user_profile`` 同款 Cypher）。

        ``end_user_id`` 为空时保留 api 的全局语义（``$end_user_id IS NULL`` 分支）。
        """

        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id) "
            "AND e.entity_type <> '生命体' "
            "AND e.name IS NOT NULL "
            "AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC LIMIT $limit"
        )
        rows = await self.neo4j.read_query(
            query,
            end_user_id=end_user_id,
            names_to_exclude=HOT_TAG_EXCLUDED_NAMES,
            limit=limit,
        )
        return [
            {"name": row.get("name"), "frequency": row.get("frequency")}
            for row in rows
            if row.get("name") is not None
        ]
