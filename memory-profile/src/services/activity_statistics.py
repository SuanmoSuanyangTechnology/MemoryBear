"""Legacy-compatible workspace activity statistics over display projections."""

import calendar
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.repositories.activity_statistics import ActivityStatisticsRepository
from src.runtime import ProfileRuntime

CATEGORIES = ("extraction", "retrieval", "reflection", "forgetting", "other")


def resolve_timezone(value: str | None) -> tuple[ZoneInfo, str]:
    name = (value or "").strip()
    if not name:
        raise ValueError("timezone is required")
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError(f"'{name}' is not a valid IANA timezone") from exc


def activity_range(
    now: datetime, zone: ZoneInfo, year: str | None, month: str | None, annual: bool
):
    if year is not None and (
        len(year) != 4 or not year.isascii() or not year.isdecimal() or int(year) == 0
    ):
        raise ValueError("year 必须是四位自然年")
    if not annual and (year is None) != (month is None):
        raise ValueError("year 和 month 必须同时传入")
    if month is not None and (
        not month.isascii() or not month.isdecimal() or not 1 <= int(month) <= 12
    ):
        raise ValueError("month 必须是 1 到 12 的整数")

    today = now.astimezone(zone).date()
    if year is None:
        first = today - timedelta(days=364 if annual else 29)
        length = 365 if annual else 30
    elif annual:
        first = date(int(year), 1, 1)
        length = 366 if calendar.isleap(first.year) else 365
    else:
        first = date(int(year), int(month), 1)
        length = calendar.monthrange(first.year, first.month)[1]

    start = datetime(first.year, first.month, first.day, tzinfo=zone).astimezone(UTC)
    last = first + timedelta(days=length - 1)
    if last == date.max:
        end = datetime.max.replace(tzinfo=UTC)
    else:
        next_day = last + timedelta(days=1)
        end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone).astimezone(UTC)
    return first, length, start, min(end, now)


def percentage(count: int, total: int) -> float:
    return (
        float(
            (Decimal(count) * 100 / Decimal(total)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        )
        if total
        else 0.0
    )


def activity_dates(first: date, length: int, zone: ZoneInfo):
    for offset in range(length):
        day = first + timedelta(days=offset)
        midnight = datetime(day.year, day.month, day.day, tzinfo=zone)
        yield day, int(midnight.timestamp() * 1000)


class ActivityStatisticsService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repository = ActivityStatisticsRepository(runtime.postgres)

    async def statistics(self, workspace_id, zone, zone_name, first, length, start, end):
        counts, user_ids = await self.repository.counts(workspace_id, zone_name, start, end, True)
        involved = await self.repository.involved_users(workspace_id, user_ids)
        totals = {key: 0 for key in CATEGORIES}
        daily = []
        for day, timestamp in activity_dates(first, length, zone):
            categories = counts.get(day, {})
            day_total = sum(categories.values())
            for key in CATEGORIES:
                totals[key] += categories.get(key, 0)
            daily.append(
                {"date": day.isoformat(), "timestamp": timestamp, "total_activities": day_total}
            )
        active_days = sum(item["total_activities"] > 0 for item in daily)
        total = sum(totals.values())
        average = (
            float(
                (Decimal(total) / Decimal(active_days)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
            if active_days
            else 0.0
        )
        return {
            "total_activities": total,
            "active_days": active_days,
            "daily_average": average,
            "peak_daily_activities": max(item["total_activities"] for item in daily),
            "involved_users": involved,
            "category_totals": totals,
            "daily_activities": daily,
        }

    async def composition(self, workspace_id, zone, zone_name, first, length, start, end):
        counts, _ = await self.repository.counts(workspace_id, zone_name, start, end, False)
        totals = {key: 0 for key in CATEGORIES}
        daily = []
        for day, timestamp in activity_dates(first, length, zone):
            categories = counts.get(day, {})
            day_total = sum(categories.values())
            for key in CATEGORIES:
                totals[key] += categories.get(key, 0)
            daily.append(
                {
                    "date": day.isoformat(),
                    "timestamp": timestamp,
                    "total_activities": day_total,
                    "categories": {
                        key: {
                            "count": categories.get(key, 0),
                            "percentage": percentage(categories.get(key, 0), day_total),
                        }
                        for key in CATEGORIES
                    },
                }
            )
        total = sum(totals.values())
        return {
            "summary": {
                "total_activities": total,
                "categories": {
                    key: {"count": totals[key], "percentage": percentage(totals[key], total)}
                    for key in CATEGORIES
                },
            },
            "daily_activities": daily,
        }
