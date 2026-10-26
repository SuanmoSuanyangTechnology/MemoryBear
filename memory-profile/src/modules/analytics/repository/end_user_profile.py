"""End-user profile queries over API-owned read-only ORM mappings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser
from src.modules.shared.end_user_scope import scoped_end_user_select

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
