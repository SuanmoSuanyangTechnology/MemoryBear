"""Write access for the API-owned ``memory_config`` table.

All INSERT/UPDATE/DELETE for this domain live here; the read repositories stay
``select``-only. Short sessions, rollback on exit, and the shared connection
pool are unchanged — DDL remains owned by the API's Alembic chain.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references import MemoryConfigRow, Workspace


class MemoryConfigMutationRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def find_by_workspace_and_name(self, workspace_id: UUID, config_name: str) -> UUID | None:
        async with self.postgres.session() as session:
            return await session.scalar(
                select(MemoryConfigRow.config_id).where(
                    MemoryConfigRow.workspace_id == workspace_id,
                    MemoryConfigRow.config_name == config_name,
                )
            )

    async def create(self, values: dict[str, Any]) -> UUID:
        """Insert one configuration and return its id."""

        now = utcnow_naive()
        payload = {**values}
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        async with self.postgres.session() as session:
            row = MemoryConfigRow(**payload)
            session.add(row)
            await session.commit()
            return row.config_id

    async def update_by_id(self, config_id: UUID, values: dict[str, Any]) -> bool:
        return await self._update(config_id, None, values, for_update=False)

    async def update_owned(
        self,
        config_id: UUID,
        workspace_id: UUID,
        values: dict[str, Any],
        *,
        for_update: bool = False,
    ) -> bool:
        return await self._update(config_id, workspace_id, values, for_update=for_update)

    async def _update(
        self,
        config_id: UUID,
        workspace_id: UUID | None,
        values: dict[str, Any],
        *,
        for_update: bool,
    ) -> bool:
        async with self.postgres.session() as session:
            statement = select(MemoryConfigRow).where(MemoryConfigRow.config_id == config_id)
            if workspace_id is not None:
                statement = statement.where(MemoryConfigRow.workspace_id == workspace_id)
            if for_update:
                statement = statement.with_for_update()
            row = (await session.execute(statement)).scalars().first()
            if row is None:
                return False
            for field, value in values.items():
                setattr(row, field, value)
            await session.commit()
            return True

    async def delete_owned(self, config_id: UUID, workspace_id: UUID) -> bool:
        async with self.postgres.session() as session:
            row = (
                (
                    await session.execute(
                        select(MemoryConfigRow).where(
                            MemoryConfigRow.config_id == config_id,
                            MemoryConfigRow.workspace_id == workspace_id,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def set_workspace_active(self, workspace_id: UUID, config_id: UUID) -> bool:
        async with self.postgres.session() as session:
            workspace = (
                (
                    await session.execute(
                        select(Workspace).where(
                            Workspace.id == workspace_id,
                            Workspace.is_active.is_(True),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if workspace is None:
                return False
            workspace.memory_config = config_id
            await session.commit()
            return True
