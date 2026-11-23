"""Read-only emotion graph queries and indexed daily PostgreSQL aggregation."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select

from src.common.vocabulary import normalize_emotion_type_or_raw
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.models.references import DialogueEmotionRaw, ImplicitEmotionsStorage


class EmotionRepository:
    def __init__(self, postgres: PostgresManager, neo4j: Neo4jManager) -> None:
        self.postgres = postgres
        self.neo4j = neo4j

    async def get_emotion_tags(
        self,
        end_user_id: str,
        emotion_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "s.end_user_id = $end_user_id",
            "s.delete_at IS NULL",
            "s.emotion_type IS NOT NULL",
        ]
        params: dict[str, Any] = {"end_user_id": end_user_id}
        if emotion_type:
            clauses.append("s.emotion_type IN $emotion_types")
            params["emotion_types"] = self._aliases(emotion_type)
        if start_date:
            clauses.append(
                "s.created_at IS NOT NULL AND datetime(s.created_at) >= datetime($start)"
            )
            params["start"] = start_date
        if end_date:
            clauses.append("s.created_at IS NOT NULL AND datetime(s.created_at) <= datetime($end)")
            params["end"] = end_date
        query = f"""
        MATCH (s:Statement)
        WHERE {" AND ".join(clauses)}
        RETURN s.emotion_type AS emotion_type, count(*) AS count,
               sum(s.emotion_intensity) AS intensity_sum,
               count(s.emotion_intensity) AS intensity_count
        """
        return await self.neo4j.read_query(query, **params)

    @staticmethod
    def _aliases(value: str) -> list[str]:
        code = normalize_emotion_type_or_raw(value)
        return [code] + [
            old for old, new in {"fear": "anxiety", "surprise": "neutral"}.items() if new == code
        ]

    async def get_emotion_wordcloud(
        self, end_user_id: str, emotion_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        # Keep grouping by keyword AND normalized emotion, as in the legacy API.
        query = """
        MATCH (s:Statement)
        WHERE s.end_user_id = $end_user_id
          AND s.emotion_keywords IS NOT NULL AND s.emotion_type IS NOT NULL
          AND ($emotion_types IS NULL OR s.emotion_type IN $emotion_types)
        WITH s, CASE s.emotion_type WHEN 'fear' THEN 'anxiety'
                 WHEN 'surprise' THEN 'neutral' ELSE s.emotion_type END AS emotion_type
        UNWIND s.emotion_keywords AS keyword
        WITH keyword, emotion_type, count(*) AS frequency,
             avg(s.emotion_intensity) AS avg_intensity
        WHERE keyword IS NOT NULL AND keyword <> ''
        RETURN keyword, frequency, emotion_type, avg_intensity
        ORDER BY frequency DESC LIMIT $limit
        """
        rows = await self.neo4j.read_query(
            query,
            end_user_id=end_user_id,
            emotion_types=self._aliases(emotion_type) if emotion_type else None,
            limit=limit,
        )
        return [
            {**row, "avg_intensity": round(float(row.get("avg_intensity") or 0), 3)} for row in rows
        ]

    async def get_emotions_in_range(
        self, end_user_id: str, time_range: str = "30d"
    ) -> list[dict[str, Any]]:
        start = datetime.now(UTC) - timedelta(days={"7d": 7, "30d": 30, "90d": 90}[time_range])
        # Health uses Statement intensity and chronological transitions, not BERT
        # Dialogue rows (which contain no intensity).
        rows = await self.neo4j.read_query(
            """
            MATCH (s:Statement)
            WHERE s.end_user_id = $end_user_id AND s.delete_at IS NULL
              AND s.emotion_type IS NOT NULL AND s.created_at IS NOT NULL
              AND datetime(s.created_at) >= datetime($start)
            RETURN s.id AS statement_id, s.emotion_type AS emotion_type,
                   s.emotion_intensity AS emotion_intensity, s.created_at AS created_at
            ORDER BY datetime(s.created_at) ASC
            """,
            end_user_id=end_user_id,
            start=start.isoformat().replace("+00:00", "Z"),
        )
        return [
            {**row, "emotion_type": normalize_emotion_type_or_raw(row.get("emotion_type"))}
            for row in rows
        ]

    async def suggestions(self, user_id: UUID) -> dict[str, Any] | None:
        async with self.postgres.session() as session:
            return await session.scalar(
                select(ImplicitEmotionsStorage.emotion_suggestions)
                .where(ImplicitEmotionsStorage.end_user_id == str(user_id))
                .limit(1)
            )

    @staticmethod
    def _day_start(timestamp, timezone: str):
        local = func.timezone(timezone, func.timezone("UTC", timestamp))
        return func.timezone("UTC", func.timezone(timezone, func.date_trunc("day", local)))

    async def daily(
        self,
        user_id: UUID,
        timezone: str,
        *,
        page: int = 1,
        pagesize: int = 5,
        sort: str = "asc",
        start: date | None = None,
        end: date | None = None,
        recent: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Enumerate active dates by indexed max hops; aggregate selected days only."""
        zone = ZoneInfo(timezone)
        filters = [DialogueEmotionRaw.end_user_id == user_id]
        if start is not None:
            filters.append(
                DialogueEmotionRaw.created_at
                >= datetime.combine(start, time.min, tzinfo=zone)
                .astimezone(UTC)
                .replace(tzinfo=None)
            )
        if end is not None:
            filters.append(
                DialogueEmotionRaw.created_at
                < datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone)
                .astimezone(UTC)
                .replace(tzinfo=None)
            )
        async with self.postgres.session() as session:
            if recent:
                # An overview needs just two index hops, not a scan/count of all
                # historical active dates.
                days = []
                before = None
                for _ in range(2):
                    conditions = list(filters)
                    if before is not None:
                        conditions.append(DialogueEmotionRaw.created_at < before)
                    latest = await session.scalar(
                        select(func.max(DialogueEmotionRaw.created_at)).where(*conditions)
                    )
                    if latest is None:
                        break
                    day = latest.replace(tzinfo=UTC).astimezone(zone).date()
                    days.append(day)
                    before = (
                        datetime.combine(day, time.min, tzinfo=zone)
                        .astimezone(UTC)
                        .replace(tzinfo=None)
                    )
                total = len(days)
            else:
                active = (
                    select(func.max(DialogueEmotionRaw.created_at).label("ct"))
                    .where(*filters)
                    .cte("active_days", recursive=True)
                )
                hop = select(
                    select(func.max(DialogueEmotionRaw.created_at))
                    .where(
                        *filters,
                        DialogueEmotionRaw.created_at < self._day_start(active.c.ct, timezone),
                    )
                    .scalar_subquery()
                ).where(active.c.ct.is_not(None))
                active = active.union_all(hop)
                ranked = (
                    select(
                        active.c.ct,
                        func.count().over().label("total"),
                        func.row_number()
                        .over(order_by=active.c.ct.desc() if sort == "desc" else active.c.ct.asc())
                        .label("rank"),
                    )
                    .where(active.c.ct.is_not(None))
                    .subquery()
                )
                rows = (
                    await session.execute(
                        select(ranked.c.ct, ranked.c.total).where(
                            ranked.c.rank.between((page - 1) * pagesize + 1, page * pagesize)
                        )
                    )
                ).all()
                total = (
                    int(rows[0].total)
                    if rows
                    else int(
                        (
                            await session.scalar(
                                select(func.count())
                                .select_from(active)
                                .where(active.c.ct.is_not(None))
                            )
                        )
                        or 0
                    )
                )
                days = [row.ct.replace(tzinfo=UTC).astimezone(zone).date() for row in rows]
            if not days:
                return [], total
            lo = (
                datetime.combine(min(days), time.min, tzinfo=zone)
                .astimezone(UTC)
                .replace(tzinfo=None)
            )
            hi = (
                datetime.combine(max(days) + timedelta(days=1), time.min, tzinfo=zone)
                .astimezone(UTC)
                .replace(tzinfo=None)
            )
            local_day = cast(
                func.timezone(timezone, func.timezone("UTC", DialogueEmotionRaw.created_at)), Date
            )
            records = (
                await session.execute(
                    select(local_day, DialogueEmotionRaw.emotion, func.count())
                    .where(
                        DialogueEmotionRaw.end_user_id == user_id,
                        DialogueEmotionRaw.created_at >= lo,
                        DialogueEmotionRaw.created_at < hi,
                    )
                    .group_by(local_day, DialogueEmotionRaw.emotion)
                )
            ).all()
        grouped: dict[date, dict[str, Any]] = {}
        for day, emotion, count in records:
            if day not in days:
                continue
            item = grouped.setdefault(day, {"stat_date": day, "dialogue_count": 0, "emotions": []})
            item["dialogue_count"] += int(count)
            item["emotions"].append({"type": emotion, "count": int(count)})
        for item in grouped.values():
            item["emotions"].sort(key=lambda emotion: emotion["count"], reverse=True)
        return [
            grouped[day] for day in sorted(grouped, reverse=sort == "desc" and not recent)
        ], total
