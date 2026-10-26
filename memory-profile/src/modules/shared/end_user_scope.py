"""The one definition of "this end user may be addressed from this workspace".

Every end-user read and write composes these predicates instead of repeating the
conditions, so the workspace boundary cannot drift between the access service,
the profile reads and the generation writes.

Shapes, deliberately different:

- :func:`active_user_in_workspace` — a specific user in a specific workspace;
  both the user and the workspace must be active. Callers join ``workspaces``.
- :func:`active_users_in_workspace` — a workspace's active users, used by list
  queries that already know the workspace. No join, no ``workspaces`` filter.
- :func:`scoped_end_user_select` — the narrow-read template over the first
  shape, so a profile read only chooses its columns.

:func:`active_workspace_exists` is the ``EXISTS`` form for ``UPDATE`` statements
and other queries that cannot carry the workspace join in their target.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Select, select
from sqlalchemy.sql.elements import ColumnElement

from src.models.references import EndUser, Workspace


def active_user_in_workspace(
    *, end_user_id: UUID, workspace_id: UUID
) -> tuple[ColumnElement[bool], ...]:
    """Predicates for one active user inside one active workspace.

    Requires a join to ``workspaces`` (``Workspace.id == EndUser.workspace_id``),
    which every caller already has for the ``Workspace.is_active`` condition.
    """

    return (
        EndUser.id == end_user_id,
        EndUser.workspace_id == workspace_id,
        EndUser.is_active.is_(True),
        Workspace.is_active.is_(True),
    )


def active_users_in_workspace(workspace_id: UUID) -> tuple[ColumnElement[bool], ...]:
    """Predicates for a workspace's active users when no workspace join is needed."""

    return (
        EndUser.workspace_id == workspace_id,
        EndUser.is_active.is_(True),
    )


def active_workspace_exists(
    workspace_id: UUID, *conditions: ColumnElement[bool]
) -> ColumnElement[bool]:
    """``EXISTS`` form of the active-workspace check for ``UPDATE`` targets.

    ``conditions`` extend the same subquery (for example the ``storage_type``
    gate), because an ``UPDATE ... WHERE`` cannot carry a workspace join.
    """

    return sa.exists(
        select(1)
        .select_from(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.is_active.is_(True),
            *conditions,
        )
    )


def scoped_end_user_select(
    *columns: Any, end_user_id: UUID, workspace_id: UUID
) -> Select:
    """``SELECT <columns>`` from one active user of one active workspace.

    The join and ``LIMIT 1`` belong with the boundary rule, not with each narrow
    read: callers only choose the column subset they need.
    """

    return (
        select(*columns)
        .select_from(EndUser)
        .join(Workspace, Workspace.id == EndUser.workspace_id)
        .where(
            *active_user_in_workspace(
                end_user_id=end_user_id,
                workspace_id=workspace_id,
            )
        )
        .limit(1)
    )
