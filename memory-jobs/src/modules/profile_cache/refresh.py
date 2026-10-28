"""Freshness rules for the periodic insight/summary cache refresh.

Ported from the API's ``classify_memory_cache_refresh``
(``api/app/core/memory/analytics/memory_insight.py``) so the periodic scanner
decides exactly as the API did:

* a missing cache column always dispatches;
* no source timestamp means there is nothing to compare, so the user is skipped;
* a cache newer than its source is already current;
* a cache inside the freshness window is skipped even when the source moved;
* otherwise the user is dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

#: Same three outcomes the API scan counts: dispatch / no change / too fresh.
RefreshDecision = Literal["dispatch", "skip_no_change", "skip_fresh"]


@dataclass(frozen=True, slots=True)
class RefreshDecisions:
    """Per-column verdict for one user; insight and summary age independently."""

    insight: RefreshDecision
    summary: RefreshDecision


def utcnow_naive() -> datetime:
    """Naive UTC "now", matching how the columns are stored."""

    return datetime.now(UTC).replace(tzinfo=None)


def as_utc_aware(value: datetime | None) -> datetime | None:
    """Interpret a datetime as UTC-aware; naive values are UTC by convention."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _classify_single(
    cache_at: datetime | None,
    source_at: datetime | None,
    now: datetime,
    fresh_window: timedelta,
) -> RefreshDecision:
    if cache_at is None:
        return "dispatch"
    if source_at is None:
        return "skip_no_change"

    cache_time = as_utc_aware(cache_at)
    source_time = as_utc_aware(source_at)
    now_time = as_utc_aware(now)
    if cache_time is None or source_time is None or now_time is None:
        return "skip_no_change"
    if cache_time > source_time:
        return "skip_no_change"
    if now_time - cache_time < fresh_window:
        return "skip_fresh"
    return "dispatch"


def _latest_source_time(*values: datetime | None) -> datetime | None:
    latest_value: datetime | None = None
    latest_aware: datetime | None = None
    for value in values:
        aware_value = as_utc_aware(value)
        if aware_value is None:
            continue
        if latest_aware is None or aware_value > latest_aware:
            latest_value = value
            latest_aware = aware_value
    return latest_value


def classify_cache_refresh(
    *,
    insight_at: datetime | None,
    summary_at: datetime | None,
    write_at: datetime | None,
    metadata_updated_at: datetime | None,
    now: datetime | None = None,
    fresh_hours: int,
) -> RefreshDecisions:
    """Decide which cache columns this user's sources have outgrown."""

    insight_source_at = _latest_source_time(write_at, metadata_updated_at)
    current_time = now or utcnow_naive()
    fresh_window = timedelta(hours=fresh_hours)

    return RefreshDecisions(
        insight=_classify_single(insight_at, insight_source_at, current_time, fresh_window),
        summary=_classify_single(summary_at, write_at, current_time, fresh_window),
    )
