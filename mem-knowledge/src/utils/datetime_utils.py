"""UTC datetime conversion helpers copied from the legacy API boundary."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_NUMERIC_TIMESTAMP_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def utcnow() -> datetime:
    """Return an aware UTC datetime."""

    return datetime.now(UTC)


def utcnow_naive() -> datetime:
    """Return a naive UTC datetime for database storage."""

    return utcnow().replace(tzinfo=None)


def as_utc_aware(value: datetime | None) -> datetime | None:
    """Interpret a naive datetime as UTC and normalize aware values to UTC."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_timestamp_ms(value: datetime | None) -> int | None:
    """Serialize a datetime as a Unix millisecond timestamp."""

    aware_value = as_utc_aware(value)
    if aware_value is None:
        return None
    return int(aware_value.timestamp() * 1000)


def to_iso_z(value: datetime | None) -> str | None:
    """Serialize a datetime as an explicit UTC ISO string."""

    aware_value = as_utc_aware(value)
    if aware_value is None:
        return None
    return aware_value.isoformat().replace("+00:00", "Z")


def parse_timestamp_to_utc_naive(timestamp: int | float | None) -> datetime | None:
    """Convert a second or millisecond timestamp to naive UTC."""

    if timestamp is None:
        return None
    if timestamp > 1e10:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)


def parse_timestamp_to_utc(timestamp: int | float | None) -> datetime | None:
    """Convert a second or millisecond timestamp to aware UTC."""

    if timestamp is None:
        return None
    if timestamp > 1e10:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, UTC)


def parse_iso_to_utc_naive(value: str | None) -> datetime | None:
    """Parse an ISO datetime and normalize it to naive UTC."""

    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def parse_metadata_time_to_utc_naive(
    value: str | int | float | datetime | None,
) -> datetime | None:
    """Normalize a legacy metadata time value to naive UTC."""

    if value is None:
        return None
    if isinstance(value, datetime):
        aware_value = as_utc_aware(value)
        return aware_value.replace(tzinfo=None) if aware_value is not None else None
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid metadata time value")
    if isinstance(value, (int, float)):
        return parse_timestamp_to_utc_naive(value)
    if not isinstance(value, str):
        raise ValueError("metadata time value must be a string, timestamp, or datetime")

    stripped = value.strip()
    if not stripped:
        return None
    numeric_part = stripped.lstrip("+-").split(".", 1)[0]
    if _NUMERIC_TIMESTAMP_RE.fullmatch(stripped) and len(numeric_part) >= 10:
        timestamp = float(stripped) if "." in stripped else int(stripped)
        return parse_timestamp_to_utc_naive(timestamp)
    return parse_iso_to_utc_naive(stripped)


__all__ = [
    "UTC",
    "as_utc_aware",
    "parse_iso_to_utc_naive",
    "parse_metadata_time_to_utc_naive",
    "parse_timestamp_to_utc",
    "parse_timestamp_to_utc_naive",
    "to_iso_z",
    "to_timestamp_ms",
    "utcnow",
    "utcnow_naive",
]
