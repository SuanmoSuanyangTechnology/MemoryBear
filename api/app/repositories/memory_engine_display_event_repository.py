"""记忆引擎展示事件 Repository

事件批量插入（ON CONFLICT DO NOTHING）和按指定时区日期 + 引擎类型聚合分页查询。
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from sqlalchemy import Date, DateTime, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.end_user_model import EndUser
from app.models.memory_engine_display_event_model import MemoryEngineDisplayEvent

logger = logging.getLogger(__name__)


class MemoryEngineDisplayEventRepository:
    """引擎展示事件数据访问层"""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def bulk_insert_events(
        self,
        events: List[MemoryEngineDisplayEvent],
    ) -> int:
        """事务批量插入引擎事件。

        使用 ON CONFLICT DO NOTHING 保证幂等。

        Args:
            events: 组装好的引擎事件列表

        Returns:
            实际插入的行数
        """
        if not events:
            return 0

        values = [
            {
                "id": e.id,
                "end_user_id": e.end_user_id,
                "workspace_id": e.workspace_id,
                "operation_id": e.operation_id,
                "engine_type": e.engine_type,
                "details": e.details,
                "occurred_at": e.occurred_at,
            }
            for e in events
        ]

        stmt = pg_insert(MemoryEngineDisplayEvent).values(values)
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_engine_display_user_type_op"
        )

        result = self.db.execute(stmt)
        self.db.commit()

        inserted = result.rowcount
        logger.info(
            f"[EngineDisplay] 批量插入完成: "
            f"attempted={len(events)}, inserted={inserted}"
        )
        return inserted

    async def query_aggregated_paginated(
        self,
        workspace_id: uuid.UUID,
        timezone: str,
        page: int,
        pagesize: int,
        end_user_id: uuid.UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按"指定时区日期 + engine_type"聚合分页查询。

        先对聚合组做分页（按 MAX(occurred_at) DESC），
        再查询当前页聚合组下的完整事件。

        - 传 ``end_user_id``：用户级查询，关联 end_users 限定 workspace_id 做越权
          隔离，按 (local_date, engine_type) 聚合；
        - 不传 ``end_user_id``：空间级查询，按冗余列 ``workspace_id`` 过滤（免 JOIN），
          按 (local_date, engine_type, end_user_id) 聚合——卡片仍按用户维度拆分，
          每个聚合组额外携带 ``end_user_id``，走 ``idx_engine_display_ws_occurred``。

        start_time/end_time 为 naive UTC datetime，按 occurred_at 闭区间先过滤
        事件、再按时区自然日聚合；卡片只统计窗口内事件（窗口可与自然日不对齐）。

        Args:
            workspace_id: 当前工作空间 ID，用于数据隔离与空间级过滤
            timezone: IANA 时区名称（已验证合法）
            page: 页码（从 1 开始）
            pagesize: 每页数量
            end_user_id: 终端用户 ID；None 表示空间级查询
            start_time: 窗口起始（naive UTC，闭区间，可选）
            end_time: 窗口结束（naive UTC，闭区间，可选）

        Returns:
            (聚合组列表, 聚合组总数)
            每个聚合组格式:
            {
                "engine_type": str,
                "local_date": date,
                "max_occurred_at": datetime,  # naive UTC
                "end_user_id": uuid.UUID | None,  # 仅空间级填充
                "events": [MemoryEngineDisplayEvent, ...]
            }
        """
        # 使用 AT TIME ZONE 转换生成本地日期
        # occurred_at 是 naive UTC，需要先声明为 UTC 再转目标时区

        is_workspace_level = end_user_id is None

        DisplayEvent = MemoryEngineDisplayEvent

        # 时区自然日表达式：occurred_at 是 naive UTC，先声明为 UTC 再转目标时区，
        # 最后截断为 date。func.timezone(zone, ts) 等价于 SQL 的
        # `ts AT TIME ZONE zone`，timezone 走绑定参数，无注入风险。
        local_date_col = cast(
            func.timezone(timezone, func.timezone("UTC", DisplayEvent.occurred_at)),
            Date,
        ).label("local_date")

        # 分组维度：用户级按 (local_date, engine_type)；
        # 空间级额外按 end_user_id 拆分，卡片仍按用户维度分组。
        group_cols = [local_date_col, DisplayEvent.engine_type]
        extra_select_cols = []
        if is_workspace_level:
            group_cols.append(DisplayEvent.end_user_id)
            extra_select_cols.append(DisplayEvent.end_user_id.label("end_user_id"))

        def _apply_scope_and_window(stmt):
            """附加作用域隔离与可选时间窗过滤（count 与 keys 查询共用）。"""
            if is_workspace_level:
                # 空间级：按冗余列过滤（免 JOIN end_users），
                # 走 idx_engine_display_ws_occurred。
                stmt = stmt.select_from(DisplayEvent).where(DisplayEvent.workspace_id == workspace_id)
            else:
                # 用户级：JOIN end_users 做归属隔离。
                stmt = (
                    stmt.select_from(DisplayEvent)
                    .join(EndUser, EndUser.id == DisplayEvent.end_user_id)
                    .where(
                        DisplayEvent.end_user_id == end_user_id,
                        EndUser.workspace_id == workspace_id,
                        EndUser.is_active.is_(True),
                    )
                )
            # 时间窗（naive UTC，闭区间）：先过滤事件，再按时区自然日聚合。
            if start_time is not None:
                stmt = stmt.where(DisplayEvent.occurred_at >= start_time)
            if end_time is not None:
                stmt = stmt.where(DisplayEvent.occurred_at <= end_time)
            return stmt

        # 聚合组子查询（按时区自然日 + engine_type[+ end_user_id]），
        # count 与 keys 查询共用。
        grouped = (
            _apply_scope_and_window(
                select(
                    local_date_col,
                    DisplayEvent.engine_type.label("engine_type"),
                    *extra_select_cols,
                    func.max(DisplayEvent.occurred_at).label("max_occurred_at"),
                )
            )
            .group_by(*group_cols)
            .subquery("grouped")
        )

        # Step 1: 统计聚合组总数
        total = (
            await self.db.execute(select(func.count()).select_from(grouped))
        ).scalar() or 0

        if total == 0:
            return [], 0

        # Step 2: 获取当前页的聚合键及其 UTC 边界，按 max_occurred_at DESC。
        # UTC 边界反算：本地日 0 点 / 次日 0 点按时区折回 naive UTC。
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
        keys_query_result = await self.db.execute(keys_stmt)
        keys_result = keys_query_result.fetchall()

        if not keys_result:
            return [], total

        # Step 3: 一次查回当前页所有聚合组的完整事件，避免逐组查询。
        group_specs = []
        event_filters = []
        for row in keys_result:
            m = row._mapping
            local_date = m["local_date"]  # date
            engine_type = m["engine_type"]  # str
            group_end_user_id = m["end_user_id"] if is_workspace_level else None
            max_occurred_at = m["max_occurred_at"]  # datetime naive UTC
            start_utc = m["start_utc"]  # datetime naive UTC
            end_utc = m["end_utc"]  # datetime naive UTC

            group_specs.append({
                "engine_type": engine_type,
                "local_date": local_date,
                "end_user_id": group_end_user_id,
                "max_occurred_at": max_occurred_at,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "events": [],
            })
            per_group = [
                MemoryEngineDisplayEvent.engine_type == engine_type,
                MemoryEngineDisplayEvent.occurred_at >= start_utc,
                MemoryEngineDisplayEvent.occurred_at < end_utc,
            ]
            if is_workspace_level:
                per_group.append(
                    MemoryEngineDisplayEvent.end_user_id == group_end_user_id
                )
            event_filters.append(and_(*per_group))

        events_stmt = select(MemoryEngineDisplayEvent)
        if is_workspace_level:
            events_stmt = events_stmt.where(
                MemoryEngineDisplayEvent.workspace_id == workspace_id,
                or_(*event_filters),
            )
        else:
            events_stmt = events_stmt.where(
                MemoryEngineDisplayEvent.end_user_id == end_user_id,
                or_(*event_filters),
            )
        # 卡片只统计窗口内事件（窗口可与自然日边界不对齐）
        if start_time is not None:
            events_stmt = events_stmt.where(MemoryEngineDisplayEvent.occurred_at >= start_time)
        if end_time is not None:
            events_stmt = events_stmt.where(MemoryEngineDisplayEvent.occurred_at <= end_time)
        events_stmt = events_stmt.order_by(MemoryEngineDisplayEvent.occurred_at.desc())

        events_result = await self.db.execute(events_stmt)
        events = list(events_result.scalars().all())

        # 用户级按 engine_type 分组即可；空间级需按 (end_user_id, engine_type) 分组，
        # 避免同一 engine_type 下不同用户的事件相互串组。
        specs_by_key: Dict[Any, List[Dict[str, Any]]] = {}
        for spec in group_specs:
            key = (
                (spec["end_user_id"], spec["engine_type"])
                if is_workspace_level
                else spec["engine_type"]
            )
            specs_by_key.setdefault(key, []).append(spec)

        # events 已按 occurred_at 倒序，依次追加可保持每组内部顺序。
        for event in events:
            key = (
                (event.end_user_id, event.engine_type)
                if is_workspace_level
                else event.engine_type
            )
            for spec in specs_by_key.get(key, []):
                if spec["start_utc"] <= event.occurred_at < spec["end_utc"]:
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
