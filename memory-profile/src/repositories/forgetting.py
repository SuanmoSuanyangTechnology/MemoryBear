"""Read-only persistence and graph queries for the forgetting domain."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.models.references import ForgetAudit, MemoryConfigRow, Workspace
from src.repositories.forgetting_queries import (
    FORGET_AUXILIARY_CANDIDATES,
    FORGET_CORE_CANDIDATES,
    FORGET_QUOTA_BREAKDOWN,
    ISO_DATETIME_PATTERN,
)


class ForgettingRepository:
    def __init__(self, postgres: PostgresManager, neo4j: Neo4jManager) -> None:
        self.postgres = postgres
        self.neo4j = neo4j

    async def lambda_mem(self, workspace_id: UUID) -> float:
        statement = (
            select(MemoryConfigRow.lambda_mem)
            .join(Workspace, Workspace.memory_config == MemoryConfigRow.config_id)
            .where(Workspace.id == workspace_id)
        )
        async with self.postgres.session() as session:
            value = await session.scalar(statement)
        return float(value) if value is not None else 0.5

    async def quota_breakdown(self, end_user_id: UUID) -> dict[str, int]:
        rows = await self.neo4j.read_query(FORGET_QUOTA_BREAKDOWN, end_user_id=str(end_user_id))
        row = rows[0] if rows else {}
        # Neo4jNodeType's values, not the lower-case aggregation aliases.
        return {
            label: int(row.get(key, 0) or 0)
            for label, key in (
                ("Statement", "statement"),
                ("Chunk", "chunk"),
                ("ExtractedEntity", "entity"),
                ("MemorySummary", "summary"),
                ("Dialogue", "dialogue"),
            )
        }

    async def forgotten_count(self, end_user_id: UUID) -> int:
        async with self.postgres.session() as session:
            return int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(ForgetAudit)
                        .where(ForgetAudit.end_user_id == end_user_id)
                    )
                )
                or 0
            )

    async def daily_trend(self, end_user_id: UUID, start_date: date) -> dict[str, int]:
        statement = (
            select(func.date(ForgetAudit.delete_at).label("day"), func.count().label("count"))
            .where(
                ForgetAudit.end_user_id == end_user_id,
                ForgetAudit.delete_at >= datetime.combine(start_date, time.min),
            )
            .group_by(func.date(ForgetAudit.delete_at))
            .order_by("day")
        )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return {row["day"].isoformat(): int(row["count"]) for row in rows}

    async def forgotten_logs(
        self, end_user_id: UUID, page: int, pagesize: int
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(ForgetAudit.node_type, ForgetAudit.content, ForgetAudit.delete_at).where(
            ForgetAudit.end_user_id == end_user_id
        )
        async with self.postgres.session() as session:
            total = int(
                (await session.scalar(select(func.count()).select_from(base.subquery()))) or 0
            )
            rows = (
                (
                    await session.execute(
                        base.order_by(ForgetAudit.delete_at.desc(), ForgetAudit.id.desc())
                        .offset((page - 1) * pagesize)
                        .limit(pagesize)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows], total

    async def core_candidates(
        self, end_user_id: UUID, budget: int, evaluated_at_ms: int, *, isolated_only: bool
    ) -> list[dict[str, Any]]:
        return await self.neo4j.read_query(
            FORGET_CORE_CANDIDATES,
            end_user_id=str(end_user_id),
            batch_size=budget,
            protection_threshold=10,
            evaluated_at_ms=evaluated_at_ms,
            isolated_only=isolated_only,
            iso_datetime_pattern=ISO_DATETIME_PATTERN,
        )

    async def auxiliary_candidates(self, end_user_id: UUID, budget: int) -> list[dict[str, Any]]:
        return await self.neo4j.read_query(
            FORGET_AUXILIARY_CANDIDATES,
            end_user_id=str(end_user_id),
            batch_size=budget,
            content_max_len=2000,
            iso_datetime_pattern=ISO_DATETIME_PATTERN,
        )
