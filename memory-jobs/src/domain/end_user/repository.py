"""Repository interface for the end-user directory."""

from __future__ import annotations

from typing import Protocol

from src.domain.end_user.model import ResolutionCandidates
from src.domain.shared.ids import EndUserId, WorkspaceId


class EndUserDirectoryRepository(Protocol):
    """Reads scoped to one workspace, never crossing its boundary."""

    def active_ids_in(self, workspace_id: WorkspaceId) -> list[EndUserId]:
        """The workspace's active users, ordered by id."""
        ...

    def resolution_candidates(
        self, end_user_id: EndUserId, workspace_id: WorkspaceId
    ) -> ResolutionCandidates:
        """The direct hit and the merge targets inside this workspace."""
        ...

    def display_name(self, end_user_id: EndUserId) -> str | None:
        """The stored display name of an active user."""
        ...
