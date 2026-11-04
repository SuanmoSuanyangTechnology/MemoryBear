"""Read-only PostgreSQL repository for implicit memory profiles."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser
from src.models.references.memory_statistics_model import ImplicitEmotionsStorage
from src.repositories.end_user import scoped_end_user_select


class ImplicitMemoryRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def get_profile(self, end_user_id: UUID, workspace_id: UUID) -> dict[str, Any] | None:
        statement = (
            select(ImplicitEmotionsStorage.implicit_profile)
            .where(
                ImplicitEmotionsStorage.end_user_id == str(end_user_id),
                self._scope(end_user_id, workspace_id),
            )
            .order_by(
                ImplicitEmotionsStorage.updated_at.desc().nulls_last(),
                ImplicitEmotionsStorage.id.desc(),
            )
            .limit(1)
        )
        async with self.postgres.session() as session:
            profile = await session.scalar(statement)
        return profile if isinstance(profile, dict) else None

    @staticmethod
    def _scope(end_user_id: UUID, workspace_id: UUID):
        return scoped_end_user_select(
            EndUser.id, end_user_id=end_user_id, workspace_id=workspace_id
        ).exists()
