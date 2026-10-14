"""Datetime helpers: the DB stores naive UTC; API responses serialize unix milliseconds."""

from __future__ import annotations

from datetime import UTC, datetime

from ..models.base import utcnow_naive

__all__ = ["as_utc_aware", "to_timestamp_ms", "utcnow_naive"]


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
