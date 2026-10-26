"""Workspace-scoped end-user lookups behind :class:`EndUserService`.

Read-only: this repository only selects, so it is listed with the other
read-only PostgreSQL repositories in the boundary script.

Questions, deliberately different in cost:

- ``user_exists_in_workspace`` — "is this user addressable here?" One direct
  index lookup on the primary key, id-only, no merge branch.
- ``resolve_user_in_workspace`` — the same question plus merge routing and the
  display columns, in one statement.
- ``active_ids_in_workspace`` — the whole workspace's target set for a batch
  generation run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select, union_all

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, EndUserMerge, Workspace
from src.modules.shared.end_user_scope import (
    active_user_in_workspace,
    active_users_in_workspace,
)


def _resolved_columns(priority: int):
    """The columns callers need, plus the branch priority.

    ``priority`` orders the compound result (0 = direct hit, 1 = merge target);
    it never leaves this repository.
    """

    return (
        EndUser.id.label("id"),
        EndUser.other_name.label("other_name"),
        sa.literal(priority).label("priority"),
    )


class EndUserRepository:
    """Answer "is this end user addressable from this workspace?" in the database."""

    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def user_exists_in_workspace(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> bool:
        """Strict membership check: active user of an active workspace.

        No merge routing and no payload columns — callers that only need a
        yes/no (interest distribution, statistics) pay one primary-key lookup.
        A merged-away id is *not* an active user, so it answers ``False``; callers
        that must route merges use :meth:`resolve_user_in_workspace`.
        """

        statement = (
            select(EndUser.id)
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(*active_user_in_workspace(end_user_id=end_user_id, workspace_id=workspace_id))
            .limit(1)
        )
        async with self.postgres.session() as session:
            return (await session.scalar(statement)) is not None

    async def active_ids_in_workspace(self, workspace_id: UUID) -> list[UUID]:
        """Active users of a workspace, ordered by id.

        Same set the API's ``get_all_by_workspace`` uses for a whole-workspace
        generation run; the ordering keeps batch output stable.
        """

        async with self.postgres.session() as session:
            result = await session.execute(
                select(EndUser.id)
                .where(*active_users_in_workspace(workspace_id))
                .order_by(EndUser.id)
            )
            return [row[0] for row in result.all()]

    async def resolve_user_in_workspace(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """Resolve an active user of an active workspace, routing merges inside it.

        One statement, one round trip: the direct hit and the merge fallback are
        the two branches of a ``UNION ALL`` ordered by branch priority, so the
        merge path no longer pays a second client/server round trip. Both
        branches keep the same boundary — the user belongs to the workspace, the
        user is active, the workspace is active, and the merge edge itself
        belongs to the workspace — and both ride existing indexes
        (``end_users`` primary key, ``end_user_merge.origin_id``). An id from
        another workspace never resolves.
        """

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
        statement = (
            union_all(direct, merged)
            .order_by(sa.literal_column("priority"))
            .limit(1)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().first()
        if row is None:
            return None
        return {"id": row["id"], "other_name": row["other_name"]}