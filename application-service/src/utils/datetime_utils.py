"""Datetime helpers: the DB stores naive UTC; API responses serialize unix milliseconds."""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = [
    "as_utc_aware",
    "parse_iso_to_utc_naive",
    "to_iso_z",
    "to_timestamp_ms",
    "utcnow_naive",
]


def utcnow_naive() -> datetime:
    """Naive UTC now for DB storage (mirrors the host helper)."""

    return datetime.now(UTC).replace(tzinfo=None)


def as_utc_aware(value: datetime | None) -> datetime | None:
    """Treat a naive datetime as UTC, per project convention."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_timestamp_ms(value: datetime | None) -> int | None:
    """datetime -> UTC unix milliseconds (uniform response time format)."""

    aware = as_utc_aware(value)
    if aware is None:
        return None
    return int(aware.timestamp() * 1000)


def to_iso_z(value: datetime | None) -> str | None:
    """Serialize a datetime to an ISO-8601 UTC string with Z suffix."""

    aware = as_utc_aware(value)
    if aware is None:
        return None
    return aware.isoformat().replace("+00:00", "Z")


def parse_iso_to_utc_naive(value: str | None) -> datetime | None:
    """Parse an ISO datetime string and normalize it to naive UTC (host parity)."""

    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)
