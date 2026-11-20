"""Authorized emotion analytics read use cases."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from src.common.errors import BizCode, ProfileError
from src.common.normalization import as_utc_naive, resolve_timezone
from src.repositories.emotion_analytics import EmotionRepository
from src.repositories.emotion_backfill_mutation import EmotionBackfillMutationRepository
from src.runtime import ProfileRuntime
from src.services.emotion_calculations import EmotionCalculations
from src.services.end_user import EndUserService

logger = logging.getLogger(__name__)


class EmotionAnalyticsService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = EmotionRepository(runtime.postgres, runtime.neo4j)
        self.backfill = EmotionBackfillMutationRepository(runtime.postgres, runtime.neo4j)
        self.redis = runtime.redis
        self.calc = EmotionCalculations(self.repo)
        self.users = EndUserService.from_postgres(runtime.postgres)

    async def authorize(self, workspace_id: UUID, value: str) -> UUID:
        try:
            user_id = UUID(value)
        except ValueError as exc:
            raise ProfileError(BizCode.INVALID_PARAMETER, "无效终端用户 ID") from exc
        if not await self.users.exists_in_workspace(user_id, workspace_id):
            raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        return user_id

    async def tags(
        self,
        workspace_id: UUID,
        value: str,
        start: str | None,
        end: str | None,
        limit: int,
        language: str,
    ):
        uid = await self.authorize(workspace_id, value)
        return await self.calc.get_emotion_tags(
            str(uid), start_date=start, end_date=end, limit=limit, language=language
        )

    async def wordcloud(self, workspace_id: UUID, value: str, limit: int):
        uid = await self.authorize(workspace_id, value)
        return await self.calc.get_emotion_wordcloud(str(uid), limit=limit)

    async def health(self, workspace_id: UUID, value: str, time_range: str):
        uid = await self.authorize(workspace_id, value)
        return await self.calc.calculate_emotion_health_index(str(uid), time_range)

    async def suggestions(self, workspace_id: UUID, value: str):
        uid = await self.authorize(workspace_id, value)
        return await self.repo.suggestions(uid)

    async def _backfill_for_timezone(self, user_id: UUID, timezone: str) -> None:
        try:
            local_now = datetime.now(UTC).astimezone(ZoneInfo(timezone))
            offset = local_now.utcoffset()
            if offset is not None and offset >= timedelta(hours=8):
                return
            local_midnight = datetime.combine(local_now.date(), time.min, local_now.tzinfo)
            end = as_utc_naive(local_midnight)
            start = await self.backfill.latest_created_at(user_id)
            if start is None or start >= end:
                return
            redis = await self.redis.client()
            # Share the deduplication key with the original API during rollout.
            if await redis.set(f"emotion_stats:backfill:{user_id}", "1", nx=True, ex=60):
                await self.backfill.backfill(user_id, start, end)
        except Exception:
            logger.warning("Realtime emotion backfill failed for user %s", user_id, exc_info=True)

    async def daily(
        self,
        workspace_id: UUID,
        value: str,
        timezone: str | None,
        language: str,
        *,
        overview: bool,
        page: int = 1,
        pagesize: int = 5,
        sort: str = "asc",
        start: date | None = None,
        end: date | None = None,
    ):
        user_id = await self.authorize(workspace_id, value)
        if not timezone or not timezone.strip():
            raise ProfileError(
                BizCode.MISSING_PARAMETER,
                "X-Timezone 不能为空",
                detail="X-Timezone header is required",
            )
        try:
            _, timezone = resolve_timezone(timezone)
        except ValueError as exc:
            raise ProfileError(
                BizCode.INVALID_PARAMETER,
                f"无效的时区: {timezone.strip()}",
                detail=str(exc),
            ) from exc
        if start and end and start > end:
            raise ProfileError(BizCode.INVALID_PARAMETER, "start_date 不能晚于 end_date")
        await self._backfill_for_timezone(user_id, timezone)
        rows, total = await self.repo.daily(
            user_id,
            timezone,
            page=page,
            pagesize=pagesize,
            sort=sort,
            start=start,
            end=end,
            recent=overview,
        )
        items = []
        for row in rows:
            item = self.calc._format_daily_row(row, language, include_summary=not overview)
            item["data_quality"] = "normal" if row["dialogue_count"] >= 5 else "too_few"
            items.append(item)
        if not overview:
            return {
                "items": items,
                "page": {
                    "page": page,
                    "pagesize": pagesize,
                    "total": total,
                    "hasnext": page * pagesize < total,
                },
            }
        if not rows:
            return {
                "data_quality": "no_data",
                "summary": {"dialogue_count": 0, "emotion_type_count": 0},
                "conclusion": None,
                "items": [],
            }
        if len(rows) == 1:
            quality = "one_day"
            conclusion = {
                "type": "low_sample",
                "title": "仅一天数据",
                "message": "仅有一天情绪数据，无法进行对比分析",
            }
        elif all(row["dialogue_count"] >= 5 for row in rows):
            quality = "normal"
            conclusion = self.calc._build_conclusion(rows[0], rows[1], language)
        else:
            quality = "too_few"
            conclusion = {
                "type": "low_sample",
                "title": "样本极少",
                "message": "最近活跃日样本不足，无法生成可靠结论",
            }
        return {
            "data_quality": quality,
            "summary": self.calc._build_summary(rows),
            "conclusion": conclusion,
            "items": items,
        }
