"""Shared end-user/workspace scope checks."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from src.common.errors import BizCode, ProfileError
from src.infrastructure.postgres import PostgresManager
from src.models.references.end_user_model import EndUser


class UserWorkspaceChecker(Protocol):
    async def check_user_in_workspace(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> bool: ...


async def user_exists_in_workspace(
    postgres: PostgresManager,
    end_user_id: UUID,
    workspace_id: UUID,
) -> bool:
    """Return whether an active end user belongs to the workspace."""

    statement = select(EndUser.id).where(
        EndUser.id == end_user_id,
        EndUser.workspace_id == workspace_id,
        EndUser.is_active.is_(True),
    )
    async with postgres.session() as session:
        return (await session.scalar(statement)) is not None


async def require_user_in_workspace(
    checker: UserWorkspaceChecker,
    *,
    end_user_id: UUID,
    workspace_id: UUID,
) -> None:
    """Raise the shared not-found error when the end user is out of scope."""

    if not await checker.check_user_in_workspace(end_user_id, workspace_id):
        raise ProfileError(
            BizCode.USER_NOT_FOUND,
            "终端用户不存在",
            detail="end_user not found in current workspace",
            status_code=404,
        )
