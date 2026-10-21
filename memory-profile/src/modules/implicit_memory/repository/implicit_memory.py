"""Read-only PostgreSQL repository for implicit memory profiles."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.common.user_scope import user_exists_in_workspace
from src.infrastructure.postgres import PostgresManager
from src.models.references.memory_implicit_model import ImplicitEmotionsStorage


class ImplicitMemoryRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def check_user_in_workspace(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> bool:
        return await user_exists_in_workspace(
            self.postgres,
            end_user_id,
            workspace_id,
        )

    async def get_profile(self, end_user_id: UUID) -> dict[str, Any] | None:
        statement = (
            select(ImplicitEmotionsStorage.implicit_profile)
            .where(ImplicitEmotionsStorage.end_user_id == str(end_user_id))
            .order_by(ImplicitEmotionsStorage.updated_at.desc())
            .limit(1)
        )
        async with self.postgres.session() as session:
            profile = await session.scalar(statement)
        return profile if isinstance(profile, dict) else None
