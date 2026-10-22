"""Unified datetime helpers.

Project convention:
- Database stores naive UTC datetime.
- Runtime calculations may use aware UTC datetime.
- Any naive datetime loaded from DB is interpreted as UTC.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
_BARE_CLOCK_LINE_RE = re.compile(
    r"(?m)^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)(?P<rest>\s+)"
)
_NUMERIC_TIMESTAMP_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def utcnow() -> datetime:
    """Return an aware UTC datetime."""
    return datetime.now(UTC)


def utcnow_naive() -> datetime:
    """Return a naive UTC datetime for DB storage."""
    return utcnow().replace(tzinfo=None)


def resolve_iana_timezone(timezone_name: str | None) -> tuple[ZoneInfo, str]:
    """Strictly parse an IANA timezone name.

    For cases where the timezone is passed explicitly by the caller (e.g. query
    parameters); empty or invalid values raise immediately to avoid silently
    aggregating data under the wrong timezone.

    Args:
        timezone_name: IANA timezone name, e.g. "Asia/Shanghai".

    Returns:
        (ZoneInfo instance, timezone name stripped of surrounding whitespace)

    Raises:
        ValueError: The timezone name is empty or not a valid IANA timezone.
    """
    normalized_name = (timezone_name or "").strip()
    if not normalized_name:
        raise ValueError("timezone is required")
    try:
        return ZoneInfo(normalized_name), normalized_name
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError(
            f"'{normalized_name}' is not a valid IANA timezone"
        ) from exc


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


def to_utc_offset_string(dt: datetime | None) -> str | None:
    """Serialize a datetime as an explicit UTC offset string."""
    aware_dt = as_utc_aware(dt)
    if aware_dt is None:
        return None
    return aware_dt.strftime("%Y-%m-%d %H:%M:%S%z")


def normalize_progress_message_timestamps(message: str | None, reference_dt: datetime | None) -> str | None:
    """Convert legacy leading HH:MM:SS progress lines to explicit UTC ISO strings."""
    if not message:
        return message

    reference = as_utc_aware(reference_dt) or utcnow()

    def _replace(match: re.Match[str]) -> str:
        normalized_dt = reference.replace(
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            microsecond=0,
        )
        return f"{to_iso_z(normalized_dt)}{match.group('rest')}"

    return _BARE_CLOCK_LINE_RE.sub(_replace, message)


def parse_timestamp_to_utc_naive(timestamp: int | float | None) -> datetime | None:
    """Convert a second/millisecond timestamp to naive UTC datetime."""
    if timestamp is None:
        return None
    if timestamp > 1e10:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)


def parse_timestamp_to_utc(timestamp: int | float | None) -> datetime | None:
    """Convert a second/millisecond timestamp to UTC datetime."""
    if timestamp is None:
        return None
    if timestamp > 1e10:
        timestamp = timestamp / 1000
    return datetime.fromtimestamp(timestamp, UTC)


def parse_iso_to_utc_naive(value: str | None) -> datetime | None:
    """Parse an ISO datetime and normalize it to naive UTC."""
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def ensure_dialog_at(dialog_at: str | None) -> str:
    """Ensure dialog_at is a valid UTC ISO 8601 string.

    Behavior:
    1. If the input is non-empty, parse it as a UTC datetime; on success return the
       normalized form.
    2. On parse failure (malformed) or empty input, fall back to the server's
       current UTC time.

    All write entry points (write_batch / write_single / write_pipeline) call this
    function so that the memory_messages.dialog_at field and the pipeline always
    carry a valid timestamp.

    Args:
        dialog_at: Caller-provided time string; may be None, empty, or any format.

    Returns:
        Normalized ISO 8601 UTC string (with +00:00 or Z suffix).
    """
    if dialog_at and dialog_at.strip():
        try:
            parsed = parse_iso_to_utc_naive(dialog_at.strip())
            if parsed is not None:
                # Convert back to aware UTC before serializing to unify the output format
                return parsed.replace(tzinfo=UTC).isoformat()
        except Exception:
            pass
    return datetime.now(UTC).isoformat()


def parse_metadata_time_to_utc_naive(value: str | int | float | datetime | None) -> datetime | None:
    """Parse metadata time values and normalize them to naive UTC.

    Metadata filters accept ISO datetime strings with an optional timezone
    offset, or Unix timestamps in seconds/milliseconds. Naive datetime strings
    keep the project convention and are interpreted as UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc_aware(value).replace(tzinfo=None)
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


def convert_neo4j_datetime_to_python(value) -> datetime | None:
    """Neo4j temporal object / string / datetime -> Python datetime; None when unparseable or None.

    No timezone normalization is performed -- aware/naive depends on the source;
    callers needing naive UTC for DB writes must normalize themselves.
    """
    if value is None:
        return None
    try:
        if hasattr(value, "to_native"):
            return value.to_native()
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
