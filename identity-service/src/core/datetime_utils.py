"""Datetime helpers shared by the management plane (host convention).

DB stores naive UTC; any naive datetime read from the DB is interpreted as UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def utcnow_naive() -> datetime:
    return utcnow().replace(tzinfo=None)


def as_utc_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_timestamp_ms(dt: datetime | None) -> int | None:
    aware_dt = as_utc_aware(dt)
    if aware_dt is None:
        return None
    return int(aware_dt.timestamp() * 1000)


def to_iso_z(dt: datetime | None) -> str | None:
    aware_dt = as_utc_aware(dt)
    if aware_dt is None:
        return None
    return aware_dt.isoformat().replace("+00:00", "Z")
