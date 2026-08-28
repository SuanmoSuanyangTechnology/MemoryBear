# -*- coding: utf-8 -*-
"""
Dialogue Emotion Raw Repository

数据访问层：对话情绪原始明细（dialogue_emotion_raw，一条对话一行）的
Upsert、时区切日聚合与覆盖边界查询。

存储口径：created_at 为 naive UTC；聚合查询传入规范化 IANA 时区名（tz），
切日聚合在 PG 内用 (created_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date
实时完成；「逐活跃日索引跳跃」的日期归类在 Python 侧用 zoneinfo 换算
（与 PG 同一 IANA 口径），UTC 边界比较一律走 (end_user_id, created_at)
复合索引。

事务由调用方控制，仓储层只使用 flush/refresh。
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import utcnow_naive
from app.models.dialogue_emotion_raw_model import DialogueEmotionRaw
from app.models.end_user_model import EndUser

logger = logging.getLogger(__name__)

# 本地日期切日表达式（naive UTC 声明为 UTC 后转目标时区取日期），
# 仅用于聚合查询的 GROUP BY / SELECT；范围过滤一律走 UTC 边界比较（保索引）
_LOCAL_DATE_EXPR = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date"

# ct 所在本地日的 UTC 起始边界（本地日 00:00 的 naive UTC），与
# _LOCAL_DATE_EXPR 同一 IANA 口径，用于「逐活跃日索引跳跃」的上界比较
_CT_DAY_SPAN_START = (
    "(date_trunc('day', {ct} AT TIME ZONE 'UTC' AT TIME ZONE :tz)"
    " AT TIME ZONE :tz AT TIME ZONE 'UTC')"
)


class DialogueEmotionRawRepository:
    """对话情绪原始明细仓储类"""

    # 单条 Upsert 语句的最大行数：单用户窗口对话数增长（重度用户日均
    # 500+）后整批上千行会放大 WAL 与索引维护开销，超过则分片写入
    UPSERT_CHUNK_SIZE = 1000

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # 写入：Upsert（Celery 任务 / 全量脚本 / 实时补数共用）
    # ------------------------------------------------------------------

    def upsert_raw_batch(self, rows: List[Dict[str, Any]]) -> int:
        """按 id 主键批量 Upsert 原始对话明细（事务由调用方提交）

        超过 UPSERT_CHUNK_SIZE 行时自动分片（同一事务内多条语句），
        避免单语句过大导致 WAL 膨胀与索引维护开销集中。

        Args:
            rows: [{id, end_user_id, created_at, emotion}] 列表，
                  created_at 为 naive UTC，end_user_id 为 uuid 字符串

        Returns:
            int: 写入行数
        """
        if not rows:
            return 0
        now = utcnow_naive()
        values = [
            {
                "id": r["id"],
                "end_user_id": str(r["end_user_id"]),
                "created_at": r["created_at"],
                "emotion": r["emotion"],
                "created_at_row": now,
            }
            for r in rows
        ]
        total = 0
        for i in range(0, len(values), self.UPSERT_CHUNK_SIZE):
            chunk = values[i : i + self.UPSERT_CHUNK_SIZE]
            stmt = pg_insert(DialogueEmotionRaw).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "end_user_id": stmt.excluded.end_user_id,
                    "created_at": stmt.excluded.created_at,
                    "emotion": stmt.excluded.emotion,
                    "created_at_row": stmt.excluded.created_at_row,
                },
            )
            self.db.execute(stmt)
            total += len(chunk)
        logger.debug(f"批量 Upsert 对话情绪明细: {total} 行")
        return total

    # ------------------------------------------------------------------
    # 查询：按时区切日聚合（查询接口用）
    # ------------------------------------------------------------------

    def get_recent_daily(
        self, end_user_id: str, tz: str, limit: int = 2
    ) -> List[Dict[str, Any]]:
        """按 tz 切日聚合，取最近 N 个活跃日的情绪分布（概览接口用）

        实现（索引跳跃 + UTC 范围聚合，保索引）：切日表达式无法下推
        索引，不对该用户全量行算表达式——逐活跃日索引跳跃（每活跃日
        一次 max(created_at)，走 end_user_id + created_at 复合索引，
        O(log n)）定位最近 N 个活跃日，再按这些活跃日的 UTC 边界做
        范围聚合；相邻活跃日之间不存在任何行，扫描量只与这 N 天的
        行数成正比，与用户历史总行数无关。

        Args:
            end_user_id: 终端用户ID（uuid字符串，直接字符串等值比较）
            tz: 规范化 IANA 时区名（如 Asia/Shanghai）
            limit: 返回的活跃日数量，默认 2

        Returns:
            List[Dict]: 按日期**升序**（旧 → 新），每项包含：
                - stat_date: datetime.date 该时区下的本地日期
                - dialogue_count: 当日带情绪的 Dialogue 总数
                - emotions: [{type, count}] 按 count 降序
        """
        if limit <= 0:
            return []
        hop_sql = text(f"""
            SELECT max(created_at)
            FROM dialogue_emotion_raw
            WHERE end_user_id = :uid
              AND created_at < {_CT_DAY_SPAN_START.format(ct=':ct')}
        """)
        tzinfo = ZoneInfo(tz)
        picked: List[date] = []
        seen: Set[date] = set()
        ct = self.get_max_created_at(end_user_id)
        while ct is not None and len(picked) < limit:
            day = ct.replace(tzinfo=timezone.utc).astimezone(tzinfo).date()
            if day not in seen:
                seen.add(day)
                picked.append(day)
            # 跳到该本地日 00:00（UTC）之前，即更早活跃日的最大行
            ct = self.db.execute(
                hop_sql, {"uid": end_user_id, "tz": tz, "ct": ct}
            ).scalar()
        if not picked:
            return []
        return self._aggregate_days(end_user_id, tz, sorted(picked))

    def get_daily_paginated(
        self,
        end_user_id: str,
        tz: str,
        page: int,
        pagesize: int,
        sort: str = "asc",
        start_utc: Optional[datetime] = None,
        end_utc: Optional[datetime] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按 tz 切日聚合并分页返回活跃日情绪分布（时间轴接口用）

        日期过滤口径（保索引）：start_utc/end_utc 为调用方按请求时区换算好的
        naive UTC 边界（本地 start_date 00:00 / 本地 end_date 次日 00:00），
        对 created_at 做范围比较，不写聚合列表达式。

        Args:
            end_user_id: 终端用户ID
            tz: 规范化 IANA 时区名
            page: 页码（1 起）
            pagesize: 每页条数
            sort: asc=日期正序（默认），desc=日期倒序
            start_utc: 可选起始 UTC 边界（含）
            end_utc: 可选结束 UTC 边界（不含）

        Returns:
            Tuple[List[Dict], int]: (当页活跃日列表(固定按日期升序组装，desc 由 service 倒序), 总活跃日数)
        """
        range_conditions = []
        params: Dict[str, Any] = {"uid": end_user_id, "tz": tz}
        if start_utc is not None:
            range_conditions.append("created_at >= :start_utc")
            params["start_utc"] = start_utc
        if end_utc is not None:
            range_conditions.append("created_at < :end_utc")
            params["end_utc"] = end_utc
        range_sql = (" AND " + " AND ".join(range_conditions)) if range_conditions else ""

        # 1) 递归索引跳跃枚举范围内的全部活跃日：每活跃日一次
        #    max(created_at)（走复合索引），替代 COUNT(DISTINCT 切日
        #    表达式) + DISTINCT 分页对该用户全量行的逐行表达式计算
        active_days_cte = f"""
            WITH RECURSIVE active_days AS (
                SELECT max(created_at) AS ct
                FROM dialogue_emotion_raw
                WHERE end_user_id = :uid{range_sql}
                UNION ALL
                SELECT (
                    SELECT max(created_at)
                    FROM dialogue_emotion_raw
                    WHERE end_user_id = :uid
                      AND created_at < {_CT_DAY_SPAN_START.format(ct='h.ct')}{range_sql}
                )
                FROM active_days h
                WHERE h.ct IS NOT NULL
            )
        """
        rn_col = "rn_desc" if sort == "desc" else "rn_asc"
        rn_lo = (page - 1) * pagesize + 1
        rn_hi = page * pagesize
        page_sql = text(f"""
            {active_days_cte}
            SELECT ct, total FROM (
                SELECT ct,
                       row_number() OVER (ORDER BY ct DESC) AS rn_desc,
                       row_number() OVER (ORDER BY ct ASC) AS rn_asc,
                       count(*) OVER () AS total
                FROM active_days
                WHERE ct IS NOT NULL
            ) ranked
            WHERE {rn_col} BETWEEN :rn_lo AND :rn_hi
        """)
        fetched = self.db.execute(
            page_sql, dict(params, rn_lo=rn_lo, rn_hi=rn_hi)
        ).fetchall()

        if not fetched:
            # 页码超出范围：单独取 total（同一递归枚举）
            count_sql = text(f"""
                {active_days_cte}
                SELECT count(*) AS total
                FROM active_days
                WHERE ct IS NOT NULL
            """)
            total = int(self.db.execute(count_sql, params).scalar() or 0)
            return [], total

        total = int(fetched[0][1])
        tzinfo = ZoneInfo(tz)
        days: List[date] = []
        seen: Set[date] = set()
        for row in fetched:
            day = row[0].replace(tzinfo=timezone.utc).astimezone(tzinfo).date()
            if day not in seen:
                seen.add(day)
                days.append(day)

        # 2) 当页活跃日的 UTC 边界范围聚合（相邻活跃日之间无任何行）
        rows = self._aggregate_days(end_user_id, tz, sorted(days))
        return rows, total

    # ------------------------------------------------------------------
    # 查询：覆盖边界 / 存在性（窗口选择 + 实时补数用）
    # ------------------------------------------------------------------

    def has_dialogue_in_utc_range(
        self, end_user_id: str, start_dt: datetime, end_dt: datetime
    ) -> bool:
        """判断用户在 [start_dt, end_dt)（naive UTC）内是否有明细行（EXISTS 命中即停）

        用户级任务窗口选择用：查「北京前天」是否有数据。
        走 end_user_id + created_at 复合索引，勿改写成聚合列表达式。
        """
        query = text("""
            SELECT EXISTS(
                SELECT 1 FROM dialogue_emotion_raw
                WHERE end_user_id = :uid
                  AND created_at >= :start_dt
                  AND created_at < :end_dt
            )
        """)
        return bool(self.db.execute(
            query, {"uid": end_user_id, "start_dt": start_dt, "end_dt": end_dt}
        ).scalar())

    def get_max_created_at(self, end_user_id: str) -> Optional[datetime]:
        """用户已入库的最大 created_at（naive UTC）——实时补数的覆盖边界

        走 end_user_id + created_at 索引（索引有序，max 直取末条），毫秒级。
        """
        query = text("""
            SELECT created_at FROM dialogue_emotion_raw
            WHERE end_user_id = :uid
            ORDER BY created_at DESC LIMIT 1
        """)
        return self.db.execute(query, {"uid": end_user_id}).scalar()

    def get_active_end_user_ids(self, active_within_hours: int = 49) -> List[str]:
        """获取近期有写入行为的用户ID（扫描器派发候选，从旧仓储迁入）

        以 write_time（最后一次记忆写入时间，UTC）过滤，与隐性记忆扫描任务
        的活跃口径保持一致。

        49h（而非 25h）是**派发名单筛选阈值**：write_time 延迟写入的用户
        （前天活跃、昨天才写上）也能被捞回，配合用户任务按 PG 判断窗口
        （平时 24h / 漏扫 48h）补齐漏派缺口。

        Returns:
            List[str]: end_user_id 字符串列表（uuid 已转 str）
        """
        threshold = utcnow_naive() - timedelta(hours=active_within_hours)
        stmt = (
            select(EndUser.id)
            .where(EndUser.write_time.isnot(None), EndUser.write_time >= threshold)
            .order_by(EndUser.id)
        )
        return [str(uid) for uid in self.db.execute(stmt).scalars().all()]

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _aggregate_days(
        self, end_user_id: str, tz: str, days: List[date]
    ) -> List[Dict[str, Any]]:
        """对指定活跃日列表做切日聚合（UTC 范围扫描，保索引）

        相邻活跃日之间不存在任何行（活跃日 = 有明细行的日期），故
        [最早活跃日 00:00, 最晚活跃日次日 00:00) 的 UTC 范围扫描只会
        命中 days 内日期的行；日期边界换算全部在 PG 内完成
        （CAST ... AT TIME ZONE），与切日表达式同一 IANA 口径。

        Returns:
            List[Dict]: 按日期升序，每项 {stat_date, dialogue_count, emotions}
        """
        lo_day, hi_day = min(days), max(days)
        agg_sql = text(f"""
            SELECT {_LOCAL_DATE_EXPR} AS stat_date,
                   emotion,
                   count(*) AS cnt
            FROM dialogue_emotion_raw
            WHERE end_user_id = :uid
              AND created_at >= (CAST(:lo_day AS timestamp) AT TIME ZONE :tz AT TIME ZONE 'UTC')
              AND created_at < (CAST(CAST(:hi_day AS date) + 1 AS timestamp) AT TIME ZONE :tz AT TIME ZONE 'UTC')
            GROUP BY 1, 2
        """)
        result = self.db.execute(
            agg_sql, {"uid": end_user_id, "tz": tz, "lo_day": lo_day, "hi_day": hi_day}
        )
        rows = self._assemble_daily_rows(result.fetchall())
        wanted = set(days)
        return [r for r in rows if r["stat_date"] in wanted]

    @staticmethod
    def _assemble_daily_rows(records: Sequence[Any]) -> List[Dict[str, Any]]:
        """将 (stat_date, emotion, cnt) 明细行组装为按日聚合结构

        Returns:
            List[Dict]: 按日期升序，每项 {stat_date, dialogue_count, emotions(按 count 降序)}
        """
        daily: Dict[Any, Dict[str, Any]] = {}
        for record in records:
            stat_date = record[0]
            emotion = record[1]
            cnt = int(record[2])
            bucket = daily.setdefault(stat_date, {"emotions": [], "dialogue_count": 0})
            bucket["emotions"].append({"type": emotion, "count": cnt})
            bucket["dialogue_count"] += cnt

        rows = []
        for stat_date in sorted(daily.keys()):
            bucket = daily[stat_date]
            emotions = sorted(
                bucket["emotions"], key=lambda x: x["count"], reverse=True
            )
            rows.append({
                "stat_date": stat_date,
                "dialogue_count": bucket["dialogue_count"],
                "emotions": emotions,
            })
        return rows
