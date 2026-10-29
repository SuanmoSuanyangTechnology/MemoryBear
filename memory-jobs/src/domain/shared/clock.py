"""Time helpers for the domain.

Cache columns are stored as naive UTC (the host tables predate timezone-aware
columns), so the domain keeps that convention in one place instead of repeating
``replace(tzinfo=None)``.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Naive UTC "now", matching how the cache columns are stored."""

    return datetime.now(UTC).replace(tzinfo=None)


def as_utc_aware(value: datetime | None) -> datetime | None:
    """Interpret a datetime as UTC-aware; naive values are UTC by convention."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
