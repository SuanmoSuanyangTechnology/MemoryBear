# -*- coding: utf-8 -*-
"""情绪统计服务（dialogue 粒度）

模块职责：
- 查询侧：数据概览（最近2活跃日 + 四种数据质量模式 + 结论）与情绪时间轴（分页），
  读取 PostgreSQL dialogue_emotion_raw 表（一条对话一行，naive UTC），
  按请求时区（X-Timezone）在 PG 内实时切日聚合，不回查 Neo4j。
- 实时补数：慢于 UTC+8 的时区查询前触发——用 PG max(created_at)（入库进度
  水位线）与「本地昨天末」比对，有缺口则回查 Neo4j 补扫，Redis 锁去重，
  失败降级不阻塞查询（每日定时同步兜底最终一致）。
- 同步侧：从 Neo4j Dialogue 节点拉取原始对话明细（不切日），
  Upsert 到 PG。由 Celery 每日任务和全量初始化脚本调用。

display_name 语言由请求头 X-Language-Type 决定（zh/en），
conclusion 文案固定为中文（与接口文档一致）。
"""

import logging
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import utcnow_naive
from app.db import get_db_context
from app.repositories.dialogue_emotion_raw_repository import DialogueEmotionRawRepository
from app.repositories.neo4j.emotion_repository import DialogueEmotionRepository

logger = logging.getLogger(__name__)

# 实时补数去重锁（60s TTL，防并发重复补扫；不主动释放，TTL 自然过期）
_BACKFILL_LOCK_KEY_FMT = "emotion_stats:backfill:{end_user_id}"
_BACKFILL_LOCK_TTL_SEC = 60

# 北京时间偏移（时区分层判断基准：UTC+8 及更早时区跳过实时补数）
_BEIJING_OFFSET = timedelta(hours=8)


class EmotionStatsService:
    """情绪统计服务（dialogue 粒度）"""

    # 样本极少阈值（接口文档 3.4：最近2活跃日 dialogue_count < 5 判为低样本）
    TOO_FEW_THRESHOLD = 5

    # 十种 BERT 情绪枚举的中英文映射（display_name 由接口层按语言实时映射，不落库）
    EMOTION_DISPLAY_NAMES = {
        "zh": {
            "joy": "愉悦",
            "anger": "愤怒",
            "anxiety": "焦虑",
            "hope": "希望",
            "neutral": "中性",
            "relief": "释然",
            "confusion": "困惑",
            "loneliness": "孤独",
            "sadness": "悲伤",
            "frustration": "挫败",
        },
        "en": {
            "joy": "Joy",
            "anger": "Anger",
            "anxiety": "Anxiety",
            "hope": "Hope",
            "neutral": "Neutral",
            "relief": "Relief",
            "confusion": "Confusion",
            "loneliness": "Loneliness",
            "sadness": "Sadness",
            "frustration": "Frustration",
        },
    }

    # 情绪效价（dominant_shift 结论文案用）
    POSITIVE_EMOTIONS = {"joy", "hope", "relief"}
    NEGATIVE_EMOTIONS = {"anger", "anxiety", "sadness", "frustration", "loneliness"}

    def __init__(self, db: Session):
        self.db = db
        self.repo = DialogueEmotionRawRepository(db)

    # =====================================================================
    # 查询前实时补数（慢于 UTC+8 的时区触发）
    # =====================================================================

    @classmethod
    async def backfill_for_timezone(cls, end_user_id: str, tz_name: str) -> None:
        """查询前实时补数：判断本地昨天尾部是否已入库，有缺口则回查 Neo4j 补扫

        三层短路（设计文档 3.3.1）：
        1. UTC+8 及更早时区（快时区）直接跳过——本地昨天必然已被
           每日定时同步的北京窗口覆盖，零开销；
        2. PG max(created_at) >= 本地昨天末 → 入库进度已覆盖，放行；
        3. 否则缺口区间 [max_created_at, 本地昨天末) 回查 Neo4j 补扫并 Upsert。

        边界处理：
        - max_created_at 为 NULL（新用户无任何入库数据）：不触发补扫，
          返回现有数据（no_data），交由每日定时同步 / 全量脚本兜底；
        - 慢时区用户近期无对话（max 远早于昨天）：可能反复触发空的 Neo4j
          查询，首版接受（查询轻量），量级可观再加 Redis 空结果标记；
        - 任何失败只记日志降级，不阻塞查询响应（定时同步保证最终一致）。

        Args:
            end_user_id: 终端用户ID
            tz_name: 规范化 IANA 时区名（已由接口层校验）
        """
        try:
            now_utc = utcnow_naive()
            now_utc_aware = now_utc.replace(tzinfo=dt_timezone.utc)
            tz = ZoneInfo(tz_name)

            # 第 1 层：UTC+8 及更早时区（含 DST 时区按请求时刻实际偏移比较）跳过
            if tz.utcoffset(now_utc_aware) >= _BEIJING_OFFSET:
                return

            # 本地昨天末 = 本地今天 00:00 对应的 naive UTC 时刻
            now_local = now_utc_aware.astimezone(tz)
            local_today_start = datetime.combine(now_local.date(), time.min, tzinfo=tz)
            local_yesterday_end_utc = (
                local_today_start.astimezone(dt_timezone.utc).replace(tzinfo=None)
            )

            # 第 2 层：入库进度水位线比对（独立短事务，避免长事务）
            with get_db_context() as db:
                max_created = DialogueEmotionRawRepository(db).get_max_created_at(
                    end_user_id
                )
            if max_created is None:
                # 新用户无任何入库数据：不补，交定时任务/全量脚本兜底
                return
            if max_created >= local_yesterday_end_utc:
                # PG 担保 max 过线必完整（Upsert 按 id 幂等，水位线单调推进）
                return

            # 第 3 层：缺口区间回查 Neo4j 补扫
            # Redis 去重锁：防并发请求重复补扫（SET NX EX 60）
            from app.aioRedis import get_thread_safe_redis

            lock_key = _BACKFILL_LOCK_KEY_FMT.format(end_user_id=end_user_id)
            try:
                redis_client = get_thread_safe_redis()
                acquired = await redis_client.set(
                    lock_key, "1", nx=True, ex=_BACKFILL_LOCK_TTL_SEC
                )
            except Exception:
                logger.warning(
                    f"[EmotionStats] 实时补数 Redis 锁获取失败（降级跳过）: "
                    f"end_user_id={end_user_id}",
                    exc_info=True,
                )
                return
            if not acquired:
                # 已有并发请求在补扫，本次直接返回现有数据
                return

            await cls._do_backfill(
                end_user_id, max_created, local_yesterday_end_utc, tz_name
            )
        except Exception:
            # 总兜底：补数失败降级，不阻塞查询响应
            logger.warning(
                f"[EmotionStats] 实时补数失败（降级返回现有数据）: "
                f"end_user_id={end_user_id}, tz={tz_name}",
                exc_info=True,
            )

    @classmethod
    async def _do_backfill(
        cls,
        end_user_id: str,
        start_dt: datetime,
        end_dt: datetime,
        tz_name: str,
    ) -> None:
        """执行缺口区间 [start_dt, end_dt) 的 Neo4j 回查 + PG Upsert（独立短事务）"""
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector

        connector = Neo4jConnector()
        try:
            rows = await DialogueEmotionRepository(connector).get_raw_dialogues(
                end_user_id, start_dt, end_dt
            )
            payload = [
                {
                    "id": r["id"],
                    "end_user_id": end_user_id,
                    "created_at": r["created_at"],
                    "emotion": r["emotion"],
                }
                for r in rows
            ]
            # 独立短事务：Upsert 后立即提交关闭
            with get_db_context() as db:
                DialogueEmotionRawRepository(db).upsert_raw_batch(payload)
                db.commit()
            logger.info(
                f"[EmotionStats] 实时补数完成: end_user_id={end_user_id}, "
                f"tz={tz_name}, gap=[{start_dt}, {end_dt}), rows={len(payload)}"
            )
        finally:
            await connector.close()

    # =====================================================================
    # 查询侧：数据概览
    # =====================================================================

    def query_overview(
        self, end_user_id: str, tz_name: str, language: str = "zh"
    ) -> Dict[str, Any]:
        """数据概览：最近 2 个活跃日 + 数据质量判定 + 核心结论

        Args:
            end_user_id: 终端用户ID
            tz_name: 规范化 IANA 时区名（切日基准）
            language: display_name 语言（zh/en）

        Returns:
            Dict: data_quality / summary / conclusion / items
        """
        recent = self.repo.get_recent_daily(end_user_id, tz_name, limit=2)

        # 无任何情绪数据
        if not recent:
            return {
                "data_quality": "no_data",
                "summary": {
                    "dialogue_count": 0,
                    "emotion_type_count": 0,
                },
                "conclusion": None,
                "items": [],
            }

        # recent 已按 stat_date 升序（旧 → 新），与接口文档示例一致
        # 日级标签：顶层 too_few 只表明"任意一天不足"，前端需据此区分具体哪天样本极少
        items = []
        for row in recent:
            item = self._format_daily_row(row, language)
            item["data_quality"] = (
                "normal"
                if row["dialogue_count"] >= self.TOO_FEW_THRESHOLD
                else "too_few"
            )
            items.append(item)

        # summary：最近 2 个活跃日口径
        summary = self._build_summary(recent)

        # 数据质量判定 + 结论
        if len(recent) == 1:
            data_quality = "one_day"
            conclusion = {
                "type": "low_sample",
                "title": "仅一天数据",
                "message": "仅有一天情绪数据，无法进行对比分析",
            }
        else:
            prev_day, latest_day = recent[0], recent[1]
            both_enough = (
                prev_day["dialogue_count"] >= self.TOO_FEW_THRESHOLD
                and latest_day["dialogue_count"] >= self.TOO_FEW_THRESHOLD
            )
            if both_enough:
                data_quality = "normal"
                conclusion = self._build_conclusion(prev_day, latest_day, language)
            else:
                data_quality = "too_few"
                conclusion = {
                    "type": "low_sample",
                    "title": "样本极少",
                    "message": "最近活跃日样本不足，无法生成可靠结论",
                }

        return {
            "data_quality": data_quality,
            "summary": summary,
            "conclusion": conclusion,
            "items": items,
        }

    # =====================================================================
    # 查询侧：情绪时间轴
    # =====================================================================

    def query_timeline(
        self,
        end_user_id: str,
        tz_name: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        sort: str = "asc",
        page: int = 1,
        page_size: int = 5,
        language: str = "zh",
    ) -> Dict[str, Any]:
        """情绪时间轴：按请求时区切日聚合，分页返回全部活跃日

        断档（gaps）由前端按相邻日期自算，接口不再返回。
        日期过滤口径：本地日期边界由本层换算为 naive UTC 边界后
        对 created_at 做范围比较（保索引，不写聚合列表达式）。

        Args:
            end_user_id: 终端用户ID
            tz_name: 规范化 IANA 时区名（切日基准）
            start_date: 可选起始日期过滤（本地日期，含）
            end_date: 可选结束日期过滤（本地日期，含）
            sort: 排序方向，asc=时间正序（默认），desc=时间倒序
            page: 页码（1 起）
            page_size: 每页条数
            language: display_name 语言（zh/en）

        Returns:
            Dict: page(PageMeta: page/pagesize/total/hasnext) / items
        """
        page = max(1, page)
        page_size = max(1, page_size)

        tz = ZoneInfo(tz_name)
        start_utc = (
            datetime.combine(start_date, time.min, tzinfo=tz)
            .astimezone(dt_timezone.utc)
            .replace(tzinfo=None)
            if start_date is not None
            else None
        )
        end_utc = (
            datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
            .astimezone(dt_timezone.utc)
            .replace(tzinfo=None)
            if end_date is not None
            else None
        )

        rows, total = self.repo.get_daily_paginated(
            end_user_id,
            tz_name,
            page=page,
            pagesize=page_size,
            sort=sort,
            start_utc=start_utc,
            end_utc=end_utc,
        )

        # 仓储侧固定按日期升序组装，desc 时倒序输出
        if sort == "desc":
            rows = list(reversed(rows))

        # 日级标签与概览口径一致（该日 dialogue_count >= 5 → normal）
        items = []
        for row in rows:
            item = self._format_daily_row(row, language, include_summary=True)
            item["data_quality"] = (
                "normal"
                if row["dialogue_count"] >= self.TOO_FEW_THRESHOLD
                else "too_few"
            )
            items.append(item)
        hasnext = (page * page_size) < total

        return {
            "page": {
                "page": page,
                "pagesize": page_size,
                "total": total,
                "hasnext": hasnext,
            },
            "items": items,
        }

    # =====================================================================
    # 同步侧：Neo4j 原始对话 → PG Upsert（Celery 任务 / 初始化脚本调用）
    # =====================================================================

    @classmethod
    async def sync_range_for_user(
        cls,
        end_user_id: str,
        start_dt: datetime,
        end_dt: datetime,
        connector=None,
        close_connector: bool = False,
    ) -> Dict[str, Any]:
        """拉取用户在 [start_dt, end_dt)（naive UTC）内的原始对话明细并 Upsert 到 PG

        不做切日聚合：一行对话一行写入 dialogue_emotion_raw，
        切日/聚合由查询接口按请求时区实时完成。

        Args:
            end_user_id: 终端用户ID（uuid字符串）
            start_dt: 起始时间（naive UTC，含）
            end_dt: 结束时间（naive UTC，不含）
            connector: 可选外部传入的 Neo4jConnector（Celery 场景传共享 driver）
            close_connector: 是否在结束时关闭 connector（独立 driver 场景需关闭）

        Returns:
            Dict: total_dialogues 等
        """
        own_connector = connector is None
        if own_connector:
            from app.repositories.neo4j.neo4j_connector import Neo4jConnector
            connector = Neo4jConnector()

        try:
            neo4j_repo = DialogueEmotionRepository(connector)
            rows = await neo4j_repo.get_raw_dialogues(end_user_id, start_dt, end_dt)
            payload = [
                {
                    "id": r["id"],
                    "end_user_id": end_user_id,
                    "created_at": r["created_at"],
                    "emotion": r["emotion"],
                }
                for r in rows
            ]

            # 短 session：Upsert 后立即提交关闭
            with get_db_context() as db:
                DialogueEmotionRawRepository(db).upsert_raw_batch(payload)
                db.commit()

            result = {
                "total_dialogues": len(payload),
                "window": [start_dt.isoformat(), end_dt.isoformat()],
            }
            logger.info(
                f"情绪明细同步完成: end_user_id={end_user_id}, "
                f"window=[{start_dt}, {end_dt}), rows={result['total_dialogues']}"
            )
            return result
        finally:
            if close_connector or own_connector:
                await connector.close()

    @classmethod
    def get_yesterday_beijing_window(
        cls, now_utc: Optional[datetime] = None
    ) -> Tuple[datetime, datetime]:
        """计算北京时间「昨天」对应的 UTC 时间窗口 [start, end)

        凌晨 1:00（北京）任务跑的是截至昨天的完整数据。
        """
        now_utc = now_utc or utcnow_naive()
        beijing_today = (now_utc + timedelta(hours=8)).date()
        yesterday_beijing = beijing_today - timedelta(days=1)
        start_dt = datetime.combine(yesterday_beijing, time.min) - timedelta(hours=8)
        return start_dt, start_dt + timedelta(days=1)

    # =====================================================================
    # 内部工具
    # =====================================================================

    def _format_daily_row(
        self, row: Dict[str, Any], language: str, include_summary: bool = False
    ) -> Dict[str, Any]:
        """将聚合行 {stat_date, dialogue_count, emotions} 格式化为接口 item

        include_summary=True 时额外输出日级结论文案 summary（仅时间轴使用）。
        """
        emotions = []
        for item in (row.get("emotions") or []):
            count = int(item.get("count", 0))
            percentage = (
                round(count / row["dialogue_count"] * 100, 2)
                if row["dialogue_count"] > 0
                else 0.0
            )
            emotions.append({
                "type": item.get("type"),
                "display_name": self.get_display_name(item.get("type"), language),
                "count": count,
                "percentage": percentage,
            })
        result = {
            "date": row["stat_date"].isoformat(),
            "dialogue_count": row["dialogue_count"],
            "emotions": emotions,
        }
        if include_summary:
            result["summary"] = self._build_daily_summary(row)
        return result

    def _build_daily_summary(self, row: Dict[str, Any]) -> str:
        """日级结论文案（时间轴每个活跃日一条）

        判定优先级（阈值与概览 TOO_FEW_THRESHOLD 统一为 5）：
        1. dialogue_count < 5        → 样本极少提醒，不做结构判定
        2. 情绪种类 = 1              → 情绪类型较为单一
        3. 情绪种类 = 2 / 3          → 仅列 TOP 情绪，不带定性词
        4. 情绪种类 ≥ 4              → 情绪类型较分散 + TOP3
        TOP 情绪取该日 emotions 首位（已按 count 降序，并列取首位）。
        文案固定中文（与 conclusion 口径一致）。
        """
        if row["dialogue_count"] < self.TOO_FEW_THRESHOLD:
            return f"样本仅 {row['dialogue_count']} 条，暂不足以判断稳定的情绪结构"

        # 情绪中文名列表（emotions 已按 count 降序）
        names = [
            self.get_display_name(item.get("type"), "zh")
            for item in (row.get("emotions") or [])
            if item.get("type")
        ]
        if not names:
            # 正常数据流不会出现（同步只收集 emotion 非 null 的对话）；
            # 兜底文案与样本量无关，避免"样本仅 n 条"与实际样本量矛盾误导
            return "当日情绪数据缺失，暂无法判断情绪结构"

        if len(names) == 1:
            return f"情绪类型较为单一，以{names[0]}为主"
        if len(names) == 2:
            return f"以{names[0]}和{names[1]}为主"
        if len(names) == 3:
            return f"以{names[0]}、{names[1]}和{names[2]}为主"
        return f"情绪类型较分散，以{names[0]}、{names[1]}和{names[2]}为主"

    def _build_summary(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """头部统计：最近 N 个活跃日口径（dialogue_count 总和、情绪种类并集）"""
        dialogue_count = sum(r["dialogue_count"] for r in rows)
        emotion_types = set()
        for r in rows:
            for item in (r.get("emotions") or []):
                if item.get("type"):
                    emotion_types.add(item["type"])
        return {
            "dialogue_count": dialogue_count,
            "emotion_type_count": len(emotion_types),
        }

    def _build_conclusion(
        self, prev_day: Dict[str, Any], latest_day: Dict[str, Any], language: str
    ) -> Dict[str, Any]:
        """核心结论（仅 normal 模式调用：两天均 ≥ 阈值）

        优先级：主导情绪迁移 > 集中/分散 > 稳定。
        集中/分散的 TOP3 占比按两天合并口径计算。
        并列主导取 emotions 数组首位（已按 count 降序）。
        """
        prev_top = self._dominant_emotion(prev_day)
        latest_top = self._dominant_emotion(latest_day)

        if prev_top and latest_top and prev_top != latest_top:
            from_name = self.get_display_name(prev_top, "zh")
            to_name = self.get_display_name(latest_top, "zh")
            suffix = self._shift_valence_suffix(prev_top, latest_top)
            return {
                "type": "dominant_shift",
                "title": "主导情绪迁移",
                "message": f"从{from_name}主导转向{to_name}主导{suffix}",
                "from_emotion": prev_top,
                "to_emotion": latest_top,
            }

        # TOP3 占比（两天合并）
        top3_ratio = self._combined_top3_ratio(prev_day, latest_day)
        if top3_ratio is not None:
            if top3_ratio >= 0.70:
                return {
                    "type": "concentrated",
                    "title": "情绪集中",
                    "message": f"最近两个活跃日 TOP3 情绪占比达 {round(top3_ratio * 100, 2)}%，情绪结构较为集中",
                }
            if top3_ratio < 0.40:
                return {
                    "type": "scattered",
                    "title": "情绪分散",
                    "message": f"最近两个活跃日 TOP3 情绪占比仅 {round(top3_ratio * 100, 2)}%，情绪结构较为分散",
                }

        dominant_name = self.get_display_name(latest_top, "zh") if latest_top else ""
        return {
            "type": "stable",
            "title": "情绪稳定",
            "message": f"连续两个活跃日均以{dominant_name}为主导，情绪状态保持稳定",
        }

    @staticmethod
    def _dominant_emotion(row: Dict[str, Any]) -> Optional[str]:
        """取主导情绪 type（emotions 已按 count 降序，并列取首位）"""
        emotions = row.get("emotions") or []
        if not emotions:
            return None
        return emotions[0].get("type")

    @staticmethod
    def _combined_top3_ratio(
        prev_day: Dict[str, Any], latest_day: Dict[str, Any]
    ) -> Optional[float]:
        """两天合并后的 TOP3 情绪占比（0~1），无数据返回 None"""
        merged: Dict[str, int] = {}
        for row in (prev_day, latest_day):
            for item in (row.get("emotions") or []):
                etype = item.get("type")
                if etype:
                    merged[etype] = merged.get(etype, 0) + int(item.get("count", 0))
        total = sum(merged.values())
        if total <= 0:
            return None
        top3 = sorted(merged.values(), reverse=True)[:3]
        return sum(top3) / total

    @classmethod
    def _shift_valence_suffix(cls, from_emotion: str, to_emotion: str) -> str:
        """迁移文案的情绪效价后缀"""
        if to_emotion in cls.POSITIVE_EMOTIONS and from_emotion not in cls.POSITIVE_EMOTIONS:
            return "，情绪状态明显改善"
        if to_emotion in cls.NEGATIVE_EMOTIONS and from_emotion not in cls.NEGATIVE_EMOTIONS:
            return "，情绪状态有所回落"
        return ""

    @classmethod
    def get_display_name(cls, emotion_type: Optional[str], language: str) -> str:
        """情绪英文枚举 → 指定语言的展示名（未知类型回退英文枚举本身）"""
        if not emotion_type:
            return ""
        names = cls.EMOTION_DISPLAY_NAMES.get(language) or cls.EMOTION_DISPLAY_NAMES["zh"]
        return names.get(emotion_type, emotion_type)
