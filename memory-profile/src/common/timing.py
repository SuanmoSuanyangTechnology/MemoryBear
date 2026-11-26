"""Time helpers shared across repositories and services.

Monotonic-clock arithmetic backs budgeted calls; ``utcnow_naive`` supplies the
tz-naive UTC wall clock the API's column defaults expect.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


def remaining_seconds(started_at: float, budget_seconds: float) -> float:
    """Seconds left in a budget measured from ``time.monotonic()``.

    The result is clamped at zero, so an exhausted budget is always ``<= 0``
    without callers re-implementing the subtraction.
    """

    return max(0.0, budget_seconds - (time.monotonic() - started_at))


def utcnow_naive() -> datetime:
    """UTC ``datetime`` without tzinfo, matching the API column defaults."""

    return datetime.now(UTC).replace(tzinfo=None)


def now_ms() -> int:
    """Current wall-clock time as epoch milliseconds (the legacy ``time`` field)."""

    return int(time.time() * 1000)


def as_utc_aware(dt: datetime | None) -> datetime | None:
    """Interpret a datetime as UTC-aware.

    Naive datetime are treated as UTC by project convention.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_timestamp_ms(dt: datetime | None) -> int | None:
    """Serialize a datetime to a UTC millisecond timestamp."""
    aware_dt = as_utc_aware(dt)
    if aware_dt is None:
        return None
    return int(aware_dt.timestamp() * 1000)


def to_iso_z(dt: datetime | None) -> str | None:
    """Serialize a datetime to an ISO-8601 UTC string with Z suffix."""
    aware_dt = as_utc_aware(dt)
    if aware_dt is None:
        return None
    return aware_dt.isoformat().replace("+00:00", "Z")
