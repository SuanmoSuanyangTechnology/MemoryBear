"""记忆引擎展示事件 Repository

事件批量插入（ON CONFLICT DO NOTHING）和按指定时区日期 + 引擎类型聚合分页查询。
"""

import logging
import uuid
from typing import Any, Dict, List, Tuple

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

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

        values = []
        for e in events:
            values.append({
                "id": e.id,
                "end_user_id": e.end_user_id,
                "operation_id": e.operation_id,
                "engine_type": e.engine_type,
                "details": e.details,
                "occurred_at": e.occurred_at,
            })

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
        end_user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        timezone: str,
        page: int,
        pagesize: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按"指定时区日期 + engine_type"聚合分页查询。

        先对聚合组做分页（按 MAX(occurred_at) DESC），
        再查询当前页聚合组下的完整事件。

        所有查询均关联 end_users 并限定 workspace_id，
        防止跨工作空间越权读取。

        Args:
            end_user_id: 终端用户 ID
            workspace_id: 当前工作空间 ID，用于数据隔离
            timezone: IANA 时区名称（已验证合法）
            page: 页码（从 1 开始）
            pagesize: 每页数量

        Returns:
            (聚合组列表, 聚合组总数)
            每个聚合组格式:
            {
                "engine_type": str,
                "local_date": date,
                "max_occurred_at": datetime,  # naive UTC
                "events": [MemoryEngineDisplayEvent, ...]
            }
        """
        # 使用 AT TIME ZONE 转换生成本地日期
        # occurred_at 是 naive UTC，需要先声明为 UTC 再转目标时区

        # Step 1: 统计聚合组总数
        count_sql = text("""
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM memory_engine_display_records r
                JOIN end_users u ON u.id = r.end_user_id
                WHERE r.end_user_id = CAST(:user_id AS uuid)
                  AND u.workspace_id = CAST(:workspace_id AS uuid)
                  AND u.is_active = true
                GROUP BY (r.occurred_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date, r.engine_type
            ) sub
        """)
        total_result = await self.db.execute(
            count_sql,
            {"user_id": end_user_id, "workspace_id": workspace_id, "tz": timezone},
        )
        total = total_result.scalar() or 0

        if total == 0:
            return [], 0

        # Step 2: 获取当前页的聚合键及其 UTC 边界，按 max_occurred_at DESC
        offset = (page - 1) * pagesize
        keys_sql = text("""
            WITH grouped AS (
                SELECT
                    (r.occurred_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date AS local_date,
                    r.engine_type AS engine_type,
                    MAX(r.occurred_at) AS max_occurred_at
                FROM memory_engine_display_records r
                JOIN end_users u ON u.id = r.end_user_id
                WHERE r.end_user_id = CAST(:user_id AS uuid)
                  AND u.workspace_id = CAST(:workspace_id AS uuid)
                  AND u.is_active = true
                GROUP BY local_date, engine_type
            )
            SELECT
                local_date,
                engine_type,
                max_occurred_at,
                CAST(local_date AS timestamp)
                    AT TIME ZONE :tz AT TIME ZONE 'UTC' AS start_utc,
                (CAST(local_date AS timestamp) + interval '1 day')
                    AT TIME ZONE :tz AT TIME ZONE 'UTC' AS end_utc
            FROM grouped
            ORDER BY max_occurred_at DESC, engine_type ASC
            LIMIT :limit OFFSET :offset
        """)
        keys_query_result = await self.db.execute(
            keys_sql,
            {
                "user_id": end_user_id,
                "workspace_id": workspace_id,
                "tz": timezone,
                "limit": pagesize,
                "offset": offset,
            },
        )
        keys_result = keys_query_result.fetchall()

        if not keys_result:
            return [], total

        # Step 3: 一次查回当前页所有聚合组的完整事件，避免逐组查询。
        group_specs = []
        event_filters = []
        for row in keys_result:
            local_date = row[0]  # date
            engine_type = row[1]  # str
            max_occurred_at = row[2]  # datetime naive UTC
            start_utc = row[3]  # datetime naive UTC
            end_utc = row[4]  # datetime naive UTC

            group_specs.append({
                "engine_type": engine_type,
                "local_date": local_date,
                "max_occurred_at": max_occurred_at,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "events": [],
            })
            event_filters.append(
                and_(
                    MemoryEngineDisplayEvent.engine_type == engine_type,
                    MemoryEngineDisplayEvent.occurred_at >= start_utc,
                    MemoryEngineDisplayEvent.occurred_at < end_utc,
                )
            )

        events_result = await self.db.execute(
            select(MemoryEngineDisplayEvent)
            .where(
                MemoryEngineDisplayEvent.end_user_id == end_user_id,
                or_(*event_filters),
            )
            .order_by(MemoryEngineDisplayEvent.occurred_at.desc())
        )
        events = list(events_result.scalars().all())

        specs_by_engine = {}
        for spec in group_specs:
            specs_by_engine.setdefault(spec["engine_type"], []).append(spec)

        # events 已按 occurred_at 倒序，依次追加可保持每组内部顺序。
        for event in events:
            for spec in specs_by_engine.get(event.engine_type, []):
                if spec["start_utc"] <= event.occurred_at < spec["end_utc"]:
                    spec["events"].append(event)
                    break

        groups = [
            {
                "engine_type": spec["engine_type"],
                "local_date": spec["local_date"],
                "max_occurred_at": spec["max_occurred_at"],
                "events": spec["events"],
            }
            for spec in group_specs
        ]

        return groups, total
