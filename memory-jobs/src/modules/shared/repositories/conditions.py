"""Reusable SQL conditions for active-user and active-workspace scoping.

Both the end-user reads and the generation writes scope on the same facts, so
the predicates live in one place instead of being restated per query.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select

from src.models import EndUser, Workspace


def active_user_in_workspace(
    *, end_user_id: UUID, workspace_id: UUID
) -> tuple[sa.ColumnElement[bool], ...]:
    """The user is active *and* belongs to an active workspace."""

    return (
        EndUser.id == end_user_id,
        EndUser.workspace_id == workspace_id,
        EndUser.is_active.is_(True),
        Workspace.is_active.is_(True),
    )


def active_users_in_workspace(workspace_id: UUID) -> tuple[sa.ColumnElement[bool], ...]:
    """The active users of a workspace (the batch target set)."""

    return (EndUser.workspace_id == workspace_id, EndUser.is_active.is_(True))


def active_workspace_exists(
    workspace_id: UUID, *conditions: sa.ColumnElement[bool]
) -> sa.ColumnElement[bool]:
    """The workspace is active and satisfies any extra ``conditions``."""

    return sa.exists(
        select(1)
        .select_from(Workspace)
        .where(Workspace.id == workspace_id, Workspace.is_active.is_(True), *conditions)
    )
