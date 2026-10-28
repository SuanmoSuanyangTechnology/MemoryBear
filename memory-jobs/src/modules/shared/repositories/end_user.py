"""End-user reads: the batch target set and merge-aware user resolution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select, union_all

from src.infrastructure.postgres import PostgresManager
from src.models import EndUser, EndUserInfo, EndUserMerge, Workspace
from src.modules.shared.repositories.conditions import (
    active_user_in_workspace,
    active_users_in_workspace,
)


class CacheRefreshFields(NamedTuple):
    """Source and cache timestamps the periodic scan compares per user.

    Mirrors the API's ``MemoryCacheRefreshFields`` (same columns, same order);
    ``metadata_updated_at`` comes from the outer-joined ``end_user_info`` row.
    """

    end_user_id: UUID
    workspace_id: UUID
    write_time: datetime | None
    metadata_updated_at: datetime | None
    memory_insight_updated_at: datetime | None
    user_summary_updated_at: datetime | None


def _resolved_columns(priority: int):
    """Columns plus the branch priority (0 = direct hit, 1 = merge target)."""

    return (
        EndUser.id.label("id"),
        EndUser.other_name.label("other_name"),
        sa.literal(priority).label("priority"),
    )


class EndUserRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    def active_ids_in_workspace(self, workspace_id: UUID) -> list[UUID]:
        """The workspace's target set for a batch run, ordered by id."""

        with self.postgres.session() as session:
            result = session.execute(
                select(EndUser.id)
                .where(*active_users_in_workspace(workspace_id))
                .order_by(EndUser.id)
            )
            return [row[0] for row in result.all()]

    def resolve_user_in_workspace(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """Resolve an active user of an active workspace, routing merges inside it."""

        direct = (
            select(*_resolved_columns(0))
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(*active_user_in_workspace(end_user_id=end_user_id, workspace_id=workspace_id))
            .limit(1)
        )
        merged = (
            select(*_resolved_columns(1))
            .select_from(EndUserMerge)
            .join(EndUser, EndUser.id == EndUserMerge.target_id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                EndUserMerge.origin_id == end_user_id,
                EndUserMerge.workspace_id == workspace_id,
                *active_users_in_workspace(workspace_id),
                Workspace.is_active.is_(True),
            )
            .order_by(EndUserMerge.id.desc())
            .limit(1)
        )
        statement = union_all(direct, merged).order_by(sa.literal_column("priority")).limit(1)
        with self.postgres.session() as session:
            row = session.execute(statement).mappings().first()
        if row is None:
            return None
        return {"id": row["id"], "other_name": row["other_name"]}

    def active_workspace_ids(self) -> list[UUID]:
        """Every active workspace, the scan's outer loop."""

        with self.postgres.session() as session:
            result = session.execute(select(Workspace.id).where(Workspace.is_active.is_(True)))
            return [row[0] for row in result.all()]

    def cache_refresh_rows(self, workspace_id: UUID) -> list[CacheRefreshFields]:
        """Cache/source timestamps for the workspace's active Neo4j-backed users.

        Same filter set as the API's scanner: active user, active workspace,
        and ``storage_type`` NULL-or-``neo4j`` (NULL predates the column and
        means Neo4j). One query per workspace keeps memory flat on large tenants.
        """

        statement = (
            select(
                EndUser.id,
                EndUser.workspace_id,
                EndUser.write_time,
                EndUserInfo.updated_at,
                EndUser.memory_insight_updated_at,
                EndUser.user_summary_updated_at,
            )
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
                sa.or_(Workspace.storage_type.is_(None), Workspace.storage_type == "neo4j"),
            )
        )
        with self.postgres.session() as session:
            return [CacheRefreshFields(*row) for row in session.execute(statement).all()]

    def other_name(self, end_user_id: UUID) -> str | None:
        with self.postgres.session() as session:
            return session.scalar(
                select(EndUser.other_name)
                .where(EndUser.id == end_user_id, EndUser.is_active.is_(True))
                .limit(1)
            )

