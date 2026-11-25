"""Workspace-scoped configuration validation before task publication."""

from uuid import UUID

from sqlalchemy import select

from src.models.references import MemoryConfigRow, Workspace


class OperationRepository:
    def __init__(self, postgres) -> None:
        self.postgres = postgres

    async def configuration(self, workspace_id: UUID, tenant_id: UUID, config_id: str | None):
        filters = [
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True),
            MemoryConfigRow.workspace_id == workspace_id,
        ]
        if config_id is None:
            filters.append(MemoryConfigRow.config_id == Workspace.memory_config)
        else:
            try:
                filters.append(MemoryConfigRow.config_id == UUID(config_id))
            except ValueError:
                try:
                    filters.append(MemoryConfigRow.config_id_old == int(config_id))
                except ValueError:
                    return None
        stmt = (
            select(MemoryConfigRow.config_id, Workspace.storage_type)
            .select_from(Workspace)
            .join(
                MemoryConfigRow,
                MemoryConfigRow.workspace_id == Workspace.id,
            )
            .where(*filters)
            .limit(1)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(stmt)).mappings().first()
        return dict(row) if row else None
