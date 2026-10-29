"""Freshness policy for the periodic insight/summary cache refresh.

Ported from the API's ``classify_memory_cache_refresh`` so the periodic scanner
decides exactly as the API did:

* a missing cache column always dispatches;
* no source timestamp means there is nothing to compare, so the user is skipped;
* a cache newer than its source is already current;
* a cache inside the freshness window is skipped even when the source moved;
* otherwise the user is dispatched.

Insight and summary age independently: insight tracks the latest of
``write_time`` and ``metadata_updated_at``, summary tracks ``write_time`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from src.domain.profile.read_models import RefreshCandidate
from src.domain.shared.clock import as_utc_aware


class RefreshDecision(StrEnum):
    DISPATCH = "dispatch"
    SKIP_NO_CHANGE = "skip_no_change"
    SKIP_FRESH = "skip_fresh"


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """Per-column verdict for one user."""

    insight: RefreshDecision
    summary: RefreshDecision

    @property
    def dispatches_insight(self) -> bool:
        return self.insight is RefreshDecision.DISPATCH

    @property
    def dispatches_summary(self) -> bool:
        return self.summary is RefreshDecision.DISPATCH

    @property
    def dispatches_anything(self) -> bool:
        return self.dispatches_insight or self.dispatches_summary


class CacheFreshnessPolicy:
    """Decide which cache columns a user's sources have outgrown."""

    def __init__(self, fresh_window: timedelta) -> None:
        self._fresh_window = fresh_window

    def plan(self, candidate: RefreshCandidate, now: datetime) -> RefreshPlan:
        insight_source_at = _latest_source_time(
            candidate.write_time, candidate.metadata_updated_at
        )
        return RefreshPlan(
            insight=_classify(
                candidate.insight_cached_at, insight_source_at, now, self._fresh_window
            ),
            summary=_classify(
                candidate.summary_cached_at, candidate.write_time, now, self._fresh_window
            ),
        )


def _classify(
    cache_at: datetime | None,
    source_at: datetime | None,
    now: datetime,
    fresh_window: timedelta,
) -> RefreshDecision:
    if cache_at is None:
        return RefreshDecision.DISPATCH
    if source_at is None:
        return RefreshDecision.SKIP_NO_CHANGE

    cache_time = as_utc_aware(cache_at)
    source_time = as_utc_aware(source_at)
    now_time = as_utc_aware(now)
    if cache_time is None or source_time is None or now_time is None:
        return RefreshDecision.SKIP_NO_CHANGE
    if cache_time > source_time:
        return RefreshDecision.SKIP_NO_CHANGE
    if now_time - cache_time < fresh_window:
        return RefreshDecision.SKIP_FRESH
    return RefreshDecision.DISPATCH


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
