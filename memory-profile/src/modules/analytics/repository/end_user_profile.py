"""End-user profile queries over API-owned read-only ORM mappings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, EndUserMerge, Workspace

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


def _scoped_user_statement(*columns: Any, end_user_id: UUID, workspace_id: UUID) -> Select:
    """「该用户在本 workspace，且用户与工作空间都有效」的唯一判定口径。

    三个读取方法共用它，避免「谁带哪些过滤条件」各写一遍之后悄悄漂移。
    """

    return (
        select(*columns)
        .select_from(EndUser)
        .join(Workspace, Workspace.id == EndUser.workspace_id)
        .where(
            EndUser.id == end_user_id,
            EndUser.workspace_id == workspace_id,
            EndUser.is_active.is_(True),
            Workspace.is_active.is_(True),
        )
        .limit(1)
    )


class EndUserProfileRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def end_user_exists(self, end_user_id: UUID, workspace_id: UUID) -> bool:
        """该用户是否在本 workspace 存在且有效 —— 只读主键列。

        ``end_users`` 上的 ``memory_insight`` / ``behavior_pattern`` / ``user_summary``
        / ``growth_trajectory`` 等是 LLM 长文本列，只有洞察与摘要两个用例需要；统计类
        与图谱类用例只做存在性判定，用本方法避免把整行长文本载入后丢掉（api
        ``end_user_exists_async`` 同款取舍）。
        """

        statement = _scoped_user_statement(
            EndUser.id, end_user_id=end_user_id, workspace_id=workspace_id
        )
        async with self.postgres.session() as session:
            return (await session.scalar(statement)) is not None

    async def get_memory_insight(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """洞察用例的窄读：只取 :data:`_MEMORY_INSIGHT_COLUMNS`，不捎带摘要列。"""

        statement = _scoped_user_statement(
            *_MEMORY_INSIGHT_COLUMNS, end_user_id=end_user_id, workspace_id=workspace_id
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row else None

    async def get_user_summary(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """摘要用例的窄读：只取 :data:`_USER_SUMMARY_COLUMNS`，不捎带洞察列。"""

        statement = _scoped_user_statement(
            *_USER_SUMMARY_COLUMNS, end_user_id=end_user_id, workspace_id=workspace_id
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row else None

    async def resolve_graph_end_user(self, end_user_id: UUID, workspace_id: UUID) -> UUID | None:
        """图谱用例的用户解析：命中返回自身，已合并则路由到合并目标 ID。

        存在性判定只读主键列（见 :meth:`end_user_exists`），只有未命中时才查一次合并表。
        """

        if await self.end_user_exists(end_user_id, workspace_id):
            return end_user_id

        statement = (
            select(EndUser.id)
            .select_from(EndUserMerge)
            .join(EndUser, EndUser.id == EndUserMerge.target_id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                EndUserMerge.origin_id == end_user_id,
                EndUserMerge.workspace_id == workspace_id,
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
            )
            .order_by(EndUserMerge.id.desc())
            .limit(1)
        )
        async with self.postgres.session() as session:
            resolved = await session.scalar(statement)
        return UUID(str(resolved)) if resolved is not None else None
