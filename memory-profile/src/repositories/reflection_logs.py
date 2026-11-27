"""PostgreSQL reads for reflection logs."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import RowMapping, func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references.end_user_model import EndUser
from src.models.references.memory_reflection_log_model import MemoryReflectionLog
from src.models.references.workspace_model import Workspace

_LIST_COLUMNS = (
    MemoryReflectionLog.id,
    MemoryReflectionLog.sub_problem,
    MemoryReflectionLog.trigger_type,
    MemoryReflectionLog.baseline,
    MemoryReflectionLog.strategy,
    MemoryReflectionLog.confidence,
    MemoryReflectionLog.status,
    MemoryReflectionLog.summary_text,
    MemoryReflectionLog.created_at,
)


class ReflectionLogRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def get_detail(self, log_id: UUID, workspace_id: UUID) -> RowMapping | None:
        statement = (
            select(*MemoryReflectionLog.__table__.columns)
            .join(EndUser, EndUser.id == MemoryReflectionLog.end_user_id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                MemoryReflectionLog.id == log_id,
                Workspace.id == workspace_id,
                Workspace.is_active.is_(True),
            )
        )
        async with self.postgres.session() as session:
            return (await session.execute(statement)).mappings().first()

    async def list_page(
        self,
        end_user_id: UUID,
        *,
        page: int,
        pagesize: int,
        sub_problem: str | None,
        status: str | None,
        trigger_type: str | None,
    ) -> tuple[int, Sequence[RowMapping]]:
        conditions = [MemoryReflectionLog.end_user_id == end_user_id]
        for column, value in (
            (MemoryReflectionLog.sub_problem, sub_problem),
            (MemoryReflectionLog.status, status),
            (MemoryReflectionLog.trigger_type, trigger_type),
        ):
            if value is not None:
                conditions.append(column == value)

        count = select(func.count()).select_from(MemoryReflectionLog).where(*conditions)
        statement = (
            select(*_LIST_COLUMNS)
            .where(*conditions)
            .order_by(MemoryReflectionLog.created_at.desc())
            .offset((page - 1) * pagesize)
            .limit(pagesize)
        )
        async with self.postgres.session() as session:
            total = await session.scalar(count)
            items = (await session.execute(statement)).mappings().all()
        return int(total or 0), items

    async def grouped_counts(self, end_user_id: UUID) -> list[tuple[str, str, int]]:
        statement = (
            select(MemoryReflectionLog.sub_problem, MemoryReflectionLog.status, func.count())
            .where(MemoryReflectionLog.end_user_id == end_user_id)
            .group_by(MemoryReflectionLog.sub_problem, MemoryReflectionLog.status)
        )
        async with self.postgres.session() as session:
            rows = await session.execute(statement)
            return [(sub_problem, status, count) for sub_problem, status, count in rows]
