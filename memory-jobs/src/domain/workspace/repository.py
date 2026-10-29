"""Repository interface for the Workspace aggregate."""

from __future__ import annotations

from typing import Protocol

from src.domain.shared.ids import WorkspaceId
from src.domain.workspace.model import Workspace


class WorkspaceRepository(Protocol):
    """Load one workspace; ``None`` when it does not exist."""

    def get(self, workspace_id: WorkspaceId) -> Workspace | None: ...
