"""Read-only reads behind the temporary-identity retention policy endpoint.

The policy is a workspace column plus a count of the workspace's active
temporary end users that still hold at least one memory. Both reads are scoped by
workspace id and touch no other module's tables.

Read-only: this repository only selects, so it belongs with the other read-only
PostgreSQL repositories in ``scripts/check_import_boundaries.py``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, Workspace

#: Identity status the retention policy governs (``temporary`` / ``confirmed`` / ...).
TEMPORARY_IDENTITY_STATUS = "temporary"


class WorkspaceRetentionRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def retention_days(self, workspace_id: UUID) -> int | None:
        """The workspace's configured retention days; ``None`` means no expiry."""

        statement = select(Workspace.retention_days).where(Workspace.id == workspace_id)
        async with self.postgres.session() as session:
            return await session.scalar(statement)

    async def temporary_end_user_count(self, workspace_id: UUID) -> int:
        """Active temporary identities of this workspace that hold a memory."""

        statement = (
            select(func.count(EndUser.id))
            .where(
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                EndUser.identity_status == TEMPORARY_IDENTITY_STATUS,
                EndUser.memory_count >= 1,
            )
        )
        async with self.postgres.session() as session:
            return int(await session.scalar(statement) or 0)