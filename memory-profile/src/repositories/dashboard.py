"""Dashboard read-only PostgreSQL queries; workspace filters are mandatory."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, func, or_, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, MemoryConfigRow, MemoryIncrement, Workspace
from src.models.references.dashboard_model import (
    DashboardApiKey,
    DashboardApiKeyLog,
    DashboardApp,
    DashboardAppShare,
    DashboardKnowledge,
)
from src.repositories.end_user import active_users_in_workspace, scoped_end_user_select


class DashboardRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def end_users(
        self,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        keyword: str | None,
        label: str | None,
        rag_counts: dict[str, int] | None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = list(active_users_in_workspace(workspace_id))
        if rag_counts is None:
            filters.append(EndUser.memory_count > 0)
        else:
            memory_ids = [UUID(uid) for uid, count in rag_counts.items() if count > 0]
            if not memory_ids:
                return [], 0
            if len(memory_ids) > 30000:
                raise ValueError("workspace has more than 30000 RAG hosts")
            filters.append(EndUser.id.in_(memory_ids))
        if label in ("long", "short"):
            filters.append(
                EndUser.identity_status == ("confirmed" if label == "long" else "temporary")
            )
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            filters.append(
                or_(EndUser.other_name.ilike(pattern), cast(EndUser.id, String).ilike(pattern))
            )
        base = (
            select(
                EndUser.id,
                EndUser.other_name,
                EndUser.other_id,
                EndUser.memory_count,
                EndUser.memory_tags,
                EndUser.write_time,
                EndUser.identity_features,
                EndUser.identity_status,
                Workspace.retention_days,
            )
            .outerjoin(Workspace, Workspace.id == EndUser.workspace_id)
            .where(*filters)
        )
        async with self.postgres.session() as session:
            total = int(
                (await session.scalar(select(func.count()).select_from(base.subquery()))) or 0
            )
            rows = (
                (
                    await session.execute(
                        base.order_by(EndUser.created_at.desc().nullslast(), EndUser.id.desc())
                        .offset((page - 1) * pagesize)
                        .limit(pagesize)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows], total

    async def memory_counts(
        self, workspace_id: UUID, ids: list[UUID] | None = None
    ) -> list[dict[str, Any]]:
        stmt = select(EndUser.id, EndUser.other_name, EndUser.memory_count).where(
            *active_users_in_workspace(workspace_id)
        )
        if ids is not None:
            stmt = stmt.where(EndUser.id.in_(ids))
        async with self.postgres.session() as session:
            rows = (await session.execute(stmt.order_by(EndUser.id.asc()))).mappings().all()
        return [
            {
                "end_user_id": str(row["id"]),
                "other_name": row["other_name"] or None,
                "memory_count": int(row["memory_count"] or 0),
            }
            for row in rows
        ]

    async def increments(
        self, workspace_id: UUID, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        day = func.date(MemoryIncrement.created_at)
        rank = (
            func.row_number()
            .over(
                partition_by=day,
                order_by=(MemoryIncrement.created_at.desc(), MemoryIncrement.id.desc()),
            )
            .label("rank")
        )
        ranked = (
            select(day.label("date"), MemoryIncrement.total_num, rank)
            .where(
                MemoryIncrement.workspace_id == workspace_id,
                MemoryIncrement.created_at >= start,
                MemoryIncrement.created_at <= end,
            )
            .subquery()
        )
        async with self.postgres.session() as session:
            rows = (
                (
                    await session.execute(
                        select(ranked.c.date, ranked.c.total_num)
                        .where(ranked.c.rank == 1)
                        .order_by(ranked.c.date.asc())
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def workspace_config(self, workspace_id: UUID) -> dict[str, str | None]:
        async with self.postgres.session() as session:
            row = (
                await session.execute(
                    select(Workspace.memory_config, MemoryConfigRow.config_name)
                    .outerjoin(
                        MemoryConfigRow, MemoryConfigRow.config_id == Workspace.memory_config
                    )
                    .where(Workspace.id == workspace_id)
                )
            ).first()
        return {
            "memory_config_id": str(row.memory_config) if row and row.memory_config else None,
            "memory_config_name": row.config_name if row else None,
        }

    async def chunk_summary(self, workspace_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        stmt = scoped_end_user_select(
            EndUser.user_summary,
            EndUser.rag_tags,
            EndUser.rag_personas,
            end_user_id=user_id,
            workspace_id=workspace_id,
        )
        async with self.postgres.session() as session:
            row = (await session.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def chunk_insight(self, workspace_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        stmt = scoped_end_user_select(
            EndUser.memory_insight,
            EndUser.behavior_pattern,
            EndUser.key_findings,
            EndUser.growth_trajectory,
            end_user_id=user_id,
            workspace_id=workspace_id,
        )
        async with self.postgres.session() as session:
            row = (await session.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def active_counts(self, neo4j, ids: list[str]) -> dict[str, int]:
        rows = await neo4j.read_query(
            """MATCH (n) WHERE n.end_user_id IN $ids AND n.delete_at IS NULL
            AND (n:Statement OR n:Chunk OR n:ExtractedEntity)
            RETURN n.end_user_id AS uid, count(n) AS count""",
            ids=ids,
        )
        return {row["uid"]: int(row["count"]) for row in rows}

    async def common_counters(self, workspace_id: UUID, today: datetime, now: datetime):
        """One snapshot for app/knowledge/API totals and their yesterday baselines.

        Legacy app-total deduplicates shared source apps, whereas app-change
        counts active share rows. Keep that intentional asymmetry.
        """

        def count(model, *filters):
            return select(func.count()).select_from(model).where(*filters)

        shares = (
            DashboardAppShare.target_workspace_id == workspace_id,
            DashboardAppShare.is_active.is_(True),
        )
        own = (DashboardApp.workspace_id == workspace_id, DashboardApp.is_active.is_(True))
        knowledge = (
            DashboardKnowledge.workspace_id == workspace_id,
            DashboardKnowledge.status == 1,
            DashboardKnowledge.parent_id == DashboardKnowledge.workspace_id,
        )
        shared_ids = select(DashboardAppShare.source_app_id).where(*shares)
        key_ids = select(DashboardApiKey.id).where(DashboardApiKey.workspace_id == workspace_id)
        logs = (DashboardApiKeyLog.api_key_id.in_(key_ids),)
        counters = {
            "total_app": count(
                DashboardApp,
                DashboardApp.is_active.is_(True),
                or_(DashboardApp.workspace_id == workspace_id, DashboardApp.id.in_(shared_ids)),
            ).scalar_subquery(),
            "total_knowledge": count(DashboardKnowledge, *knowledge).scalar_subquery(),
            "total_api_call": count(DashboardApiKeyLog, *logs).scalar_subquery(),
            "app_change_today": count(DashboardApp, *own).scalar_subquery()
            + count(DashboardAppShare, *shares).scalar_subquery(),
            "app_yesterday": count(
                DashboardApp, *own, DashboardApp.created_at < today
            ).scalar_subquery()
            + count(
                DashboardAppShare, *shares, DashboardAppShare.created_at < today
            ).scalar_subquery(),
            "knowledge_yesterday": count(
                DashboardKnowledge, *knowledge, DashboardKnowledge.created_at < today
            ).scalar_subquery(),
            "api_until_now": count(
                DashboardApiKeyLog, *logs, DashboardApiKeyLog.created_at < now
            ).scalar_subquery(),
            "api_yesterday": count(
                DashboardApiKeyLog, *logs, DashboardApiKeyLog.created_at < today
            ).scalar_subquery(),
        }
        async with self.postgres.session() as session:
            row = (
                (
                    await session.execute(
                        select(*(value.label(key) for key, value in counters.items()))
                    )
                )
                .mappings()
                .one()
            )
        return {key: int(value or 0) for key, value in row.items()}

    async def total_only(self, workspace_id: UUID, user_id: UUID | None) -> int:
        stmt = select(func.coalesce(func.sum(EndUser.memory_count), 0)).where(
            *active_users_in_workspace(workspace_id)
        )
        if user_id is not None:
            stmt = stmt.where(EndUser.id == user_id)
        async with self.postgres.session() as session:
            return int((await session.scalar(stmt)) or 0)

    async def last_memory_total(self, workspace_id: UUID, before: datetime) -> int | None:
        stmt = (
            select(MemoryIncrement.total_num)
            .where(
                MemoryIncrement.workspace_id == workspace_id, MemoryIncrement.created_at < before
            )
            .order_by(MemoryIncrement.created_at.desc(), MemoryIncrement.id.desc())
            .limit(1)
        )
        async with self.postgres.session() as session:
            value = await session.scalar(stmt)
        return int(value) if value is not None else None
