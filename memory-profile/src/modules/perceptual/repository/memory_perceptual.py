"""Read-only PostgreSQL repository for perceptual memories."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references.end_user_model import EndUser
from src.models.references.memory_statistics_model import MemoryPerceptual


class MemoryPerceptualRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def check_user_in_workspace(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> bool:
        statement = select(EndUser.id).where(
            EndUser.id == end_user_id,
            EndUser.workspace_id == workspace_id,
            EndUser.is_active.is_(True),
        )
        async with self.postgres.session() as session:
            return (await session.scalar(statement)) is not None

    async def count_by_type(self, end_user_id: UUID) -> dict[int, int]:
        statement = (
            select(
                MemoryPerceptual.perceptual_type,
                func.count(MemoryPerceptual.id),
            )
            .where(MemoryPerceptual.end_user_id == end_user_id)
            .group_by(MemoryPerceptual.perceptual_type)
        )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).all()
        return {int(row[0]): int(row[1]) for row in rows}

    async def timeline(
        self,
        *,
        end_user_id: UUID,
        perceptual_type: int | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [MemoryPerceptual.end_user_id == end_user_id]
        if perceptual_type is not None:
            filters.append(MemoryPerceptual.perceptual_type == perceptual_type)
        async with self.postgres.session() as session:
            total = int(
                (
                    await session.scalar(
                        select(func.count(MemoryPerceptual.id)).where(*filters)
                    )
                )
                or 0
            )
            if total == 0:
                return [], 0
            statement = (
                select(MemoryPerceptual)
                .where(*filters)
                .order_by(MemoryPerceptual.created_time.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            rows = (await session.execute(statement)).scalars().all()
            return [self._row_to_dict(row) for row in rows], total

    async def latest(
        self, *, end_user_id: UUID, perceptual_type: int
    ) -> dict[str, Any] | None:
        statement = (
            select(MemoryPerceptual)
            .where(
                MemoryPerceptual.end_user_id == end_user_id,
                MemoryPerceptual.perceptual_type == perceptual_type,
            )
            .order_by(MemoryPerceptual.created_time.desc())
            .limit(1)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).scalars().first()
            return self._row_to_dict(row) if row is not None else None

    @staticmethod
    def _row_to_dict(row: MemoryPerceptual) -> dict[str, Any]:
        return {
            "id": row.id,
            "end_user_id": row.end_user_id,
            "perceptual_type": row.perceptual_type,
            "storage_service": row.storage_service,
            "file_path": row.file_path,
            "file_name": row.file_name,
            "file_ext": row.file_ext,
            "summary": row.summary,
            "meta_data": row.meta_data,
            "created_time": row.created_time,
        }
