"""The end-user service other modules call.

It currently owns the workspace boundary: "may this actor address this
resource?" The answer is a value object, and every "no" is the same ``None`` so
callers cannot tell a malformed id from a foreign or deleted user. Other
end-user use cases belong here as they appear; modules call the service, never
the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.common.normalization import parse_uuid
from src.infrastructure.postgres import PostgresManager
from src.modules.shared.repository.end_user import EndUserRepository


@dataclass(frozen=True, slots=True)
class EndUserRef:
    """An end user that passed the workspace access rule."""

    id: UUID
    workspace_id: UUID
    other_name: str | None = None


class EndUserService:
    """End-user rules other modules call; currently the workspace boundary."""

    def __init__(self, repository: EndUserRepository) -> None:
        self.repository = repository

    @classmethod
    def from_postgres(cls, postgres: PostgresManager) -> EndUserService:
        """Build the service with its default repository (the composition path)."""

        return cls(EndUserRepository(postgres))

    async def active_end_user_ids(self, workspace_id: UUID) -> list[UUID]:
        """Active end users of a workspace (api ``get_all_by_workspace`` semantics)."""

        return await self.repository.active_ids_in_workspace(workspace_id)

    async def exists_in_workspace(
        self, end_user_id: str | UUID | None, workspace_id: UUID
    ) -> bool:
        """Strict membership answer for callers that need no identity.

        One primary-key lookup, no merge branch, no payload columns. A
        merged-away id is not an active user, so it answers ``False``; callers
        that must follow merges use :meth:`resolve_in_workspace`.
        """

        user_id = self._user_id(end_user_id)
        if user_id is None:
            return False
        return await self.repository.user_exists_in_workspace(user_id, workspace_id)

    async def resolve_in_workspace(
        self, end_user_id: str | UUID | None, workspace_id: UUID
    ) -> EndUserRef | None:
        """Return the addressable user, or ``None`` for invalid/foreign/inactive ids.

        ``None`` deliberately collapses "malformed id", "no such user", "deleted",
        "merged into another workspace" and "belongs to another workspace": every
        caller treats them the same way, and collapsing them avoids turning an id
        from another tenant into a probe oracle.
        """

        user_id = self._user_id(end_user_id)
        if user_id is None:
            return None
        row = await self.repository.resolve_user_in_workspace(user_id, workspace_id)
        if row is None:
            return None
        return EndUserRef(
            id=row["id"],
            workspace_id=workspace_id,
            other_name=row["other_name"],
        )

    @staticmethod
    def _user_id(value: str | UUID | None) -> UUID | None:
        return value if isinstance(value, UUID) else parse_uuid(value)
