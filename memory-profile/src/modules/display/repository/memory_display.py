"""Read-only PostgreSQL repository for memory display records and engine events."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Date, DateTime, and_, cast, func, or_, select

from src.common.user_scope import user_exists_in_workspace
from src.infrastructure.postgres import PostgresManager
from src.models.references.end_user_model import EndUser
from src.models.references.memory_display_model import (
    MemoryDisplayRecord,
    MemoryEngineDisplayEvent,
)


class MemoryDisplayRepository:
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

    async def query_written_paginated(
        self,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._query_records_paginated(
            operation="WRITE",
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def query_retrieved_paginated(
        self,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._query_records_paginated(
            operation="RETRIEVE",
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def _query_records_paginated(
        self,
        operation: str,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        base_filter = MemoryDisplayRecord.operation == operation
        if end_user_id is not None:
            owned_by_workspace = (
                select(EndUser.id)
                .where(
                    EndUser.id == end_user_id,
                    EndUser.workspace_id == workspace_id,
                    EndUser.is_active.is_(True),
                )
                .exists()
            )
            base_filter = and_(
                base_filter,
                MemoryDisplayRecord.end_user_id == end_user_id,
                owned_by_workspace,
            )
        else:
            base_filter = and_(
                base_filter,
                MemoryDisplayRecord.workspace_id == workspace_id,
            )

        if start_time is not None:
            base_filter = and_(base_filter, MemoryDisplayRecord.occurred_at >= start_time)
        if end_time is not None:
            base_filter = and_(base_filter, MemoryDisplayRecord.occurred_at <= end_time)

        async with self.postgres.session() as session:
            count_stmt = select(func.count(MemoryDisplayRecord.id)).where(base_filter)
            count_result = await session.execute(count_stmt)
            total = count_result.scalar() or 0

            if total == 0:
                return [], 0

            offset = (page - 1) * pagesize
            items_stmt = (
                select(MemoryDisplayRecord)
                .where(base_filter)
                .order_by(
                    MemoryDisplayRecord.occurred_at.desc(),
                    MemoryDisplayRecord.id.desc(),
                )
                .offset(offset)
                .limit(pagesize)
            )
            items_result = await session.execute(items_stmt)
            records = [
                {
                    "id": record.id,
                    "end_user_id": record.end_user_id,
                    "memory_id": record.memory_id,
                    "memory_type": record.memory_type,
                    "name": record.name,
                    "content": record.content,
                    "search_mode": record.search_mode,
                    "query": record.query,
                    "occurred_at": record.occurred_at,
                }
                for record in items_result.scalars().all()
            ]
            return records, total

    async def query_engine_groups_paginated(
        self,
        workspace_id: UUID,
        timezone: str,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        is_workspace_level = end_user_id is None
        display_event = MemoryEngineDisplayEvent

        local_date_col = cast(
            func.timezone(timezone, func.timezone("UTC", display_event.occurred_at)),
            Date,
        ).label("local_date")

        group_cols = [local_date_col, display_event.engine_type]
        extra_select_cols = []
        if is_workspace_level:
            group_cols.append(display_event.end_user_id)
            extra_select_cols.append(display_event.end_user_id.label("end_user_id"))

        def _apply_scope_and_window(stmt):
            if is_workspace_level:
                stmt = stmt.select_from(display_event).where(
                    display_event.workspace_id == workspace_id
                )
            else:
                stmt = (
                    stmt.select_from(display_event)
                    .join(EndUser, EndUser.id == display_event.end_user_id)
                    .where(
                        display_event.end_user_id == end_user_id,
                        EndUser.workspace_id == workspace_id,
                        EndUser.is_active.is_(True),
                    )
                )
            if start_time is not None:
                stmt = stmt.where(display_event.occurred_at >= start_time)
            if end_time is not None:
                stmt = stmt.where(display_event.occurred_at <= end_time)
            return stmt

        grouped = (
            _apply_scope_and_window(
                select(
                    local_date_col,
                    display_event.engine_type.label("engine_type"),
                    *extra_select_cols,
                    func.max(display_event.occurred_at).label("max_occurred_at"),
                )
            )
            .group_by(*group_cols)
            .subquery("grouped")
        )

        async with self.postgres.session() as session:
            count_stmt = select(func.count()).select_from(grouped)
            total = (await session.execute(count_stmt)).scalar() or 0
            if total == 0:
                return [], 0

            offset = (page - 1) * pagesize
            local_ts = cast(grouped.c.local_date, DateTime)
            start_utc = func.timezone(
                "UTC", func.timezone(timezone, local_ts)
            ).label("start_utc")
            end_utc = func.timezone(
                "UTC", func.timezone(timezone, local_ts + timedelta(days=1))
            ).label("end_utc")

            keys_stmt = (
                select(
                    grouped.c.local_date,
                    grouped.c.engine_type,
                    *([grouped.c.end_user_id] if is_workspace_level else []),
                    grouped.c.max_occurred_at,
                    start_utc,
                    end_utc,
                )
                .order_by(
                    grouped.c.max_occurred_at.desc(),
                    grouped.c.engine_type.asc(),
                )
                .limit(pagesize)
                .offset(offset)
            )
            keys_query_result = await session.execute(keys_stmt)
            keys_result = keys_query_result.fetchall()

            if not keys_result:
                return [], total

            group_specs = []
            event_filters = []
            for row in keys_result:
                mapping = row._mapping
                local_date = mapping["local_date"]
                engine_type = mapping["engine_type"]
                group_end_user_id = mapping["end_user_id"] if is_workspace_level else None
                max_occurred_at = mapping["max_occurred_at"]
                start_u = mapping["start_utc"]
                end_u = mapping["end_utc"]

                group_specs.append({
                    "engine_type": engine_type,
                    "local_date": local_date,
                    "end_user_id": group_end_user_id,
                    "max_occurred_at": max_occurred_at,
                    "start_utc": start_u,
                    "end_utc": end_u,
                    "events": [],
                })
                per_group = [
                    display_event.engine_type == engine_type,
                    display_event.occurred_at >= start_u,
                    display_event.occurred_at < end_u,
                ]
                if is_workspace_level:
                    per_group.append(display_event.end_user_id == group_end_user_id)
                event_filters.append(and_(*per_group))

            events_stmt = select(display_event)
            if is_workspace_level:
                events_stmt = events_stmt.where(
                    display_event.workspace_id == workspace_id,
                    or_(*event_filters),
                )
            else:
                events_stmt = events_stmt.where(
                    display_event.end_user_id == end_user_id,
                    or_(*event_filters),
                )
            if start_time is not None:
                events_stmt = events_stmt.where(display_event.occurred_at >= start_time)
            if end_time is not None:
                events_stmt = events_stmt.where(display_event.occurred_at <= end_time)
            events_stmt = events_stmt.order_by(display_event.occurred_at.desc())

            events_result = await session.execute(events_stmt)
            events = [
                {
                    "id": event.id,
                    "end_user_id": event.end_user_id,
                    "workspace_id": event.workspace_id,
                    "engine_type": event.engine_type,
                    "details": event.details,
                    "occurred_at": event.occurred_at,
                }
                for event in events_result.scalars().all()
            ]

        specs_by_key: dict[Any, list[dict[str, Any]]] = {}
        for spec in group_specs:
            key = (
                (spec["end_user_id"], spec["engine_type"])
                if is_workspace_level
                else spec["engine_type"]
            )
            specs_by_key.setdefault(key, []).append(spec)

        for event in events:
            key = (
                (event["end_user_id"], event["engine_type"])
                if is_workspace_level
                else event["engine_type"]
            )
            for spec in specs_by_key.get(key, []):
                if spec["start_utc"] <= event["occurred_at"] < spec["end_utc"]:
                    spec["events"].append(event)
                    break

        groups = [
            {
                "engine_type": spec["engine_type"],
                "local_date": spec["local_date"],
                "end_user_id": spec["end_user_id"],
                "max_occurred_at": spec["max_occurred_at"],
                "events": spec["events"],
            }
            for spec in group_specs
        ]
        return groups, total
