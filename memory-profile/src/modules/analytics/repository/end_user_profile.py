"""End-user profile queries over API-owned read-only ORM mappings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, EndUserMerge, Workspace

_PROFILE_COLUMNS = (
    EndUser.id,
    EndUser.workspace_id,
    EndUser.memory_insight,
    EndUser.behavior_pattern,
    EndUser.key_findings,
    EndUser.growth_trajectory,
    EndUser.memory_insight_updated_at,
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

    async def get_end_user(self, end_user_id: UUID, workspace_id: UUID) -> dict[str, Any] | None:
        statement = (
            select(*_PROFILE_COLUMNS)
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
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return dict(row) if row else None

    async def resolve_graph_end_user(self, end_user_id: UUID, workspace_id: UUID) -> UUID | None:
        direct = await self.get_end_user(end_user_id, workspace_id)
        if direct is not None:
            return UUID(str(direct["id"]))

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
