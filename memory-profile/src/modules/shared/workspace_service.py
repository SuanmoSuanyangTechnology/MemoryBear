"""The workspace row other modules read.

Reading a workspace is not analytics-specific: validation needs ``is_active`` and
``storage_type``, model resolution needs ``is_default_config``, ``memory_config``
and the six model columns, and display code needs ``name``. One service owns the
row shape so a new caller does not invent a narrower query that drifts.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.infrastructure.postgres import PostgresManager
from src.modules.shared.repository.workspace import WorkspaceRepository


class WorkspaceService:
    """Fetch one workspace row, or ``None`` when the id does not exist."""

    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    @classmethod
    def from_postgres(cls, postgres: PostgresManager) -> WorkspaceService:
        """Build the service with its default repository (the composition path)."""

        return cls(WorkspaceRepository(postgres))

    async def row(self, workspace_id: UUID) -> dict[str, Any] | None:
        return await self.repository.workspace_row(workspace_id)
