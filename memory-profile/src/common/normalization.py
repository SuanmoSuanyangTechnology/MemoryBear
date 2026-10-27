"""Dependency-free normalization helpers shared across Memory modules.

These functions encode project-wide conventions (naive timestamps are UTC,
neo4j temporals are converted before serialization) rather than analytics
rules, so they live here instead of in a domain module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID


def parse_uuid(value: object) -> UUID | None:
    """Return ``value`` as a UUID, or ``None`` when it is not a valid UUID string."""

    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def parse_int(value: object) -> int | None:
    """Return ``value`` as an int, or ``None`` when it cannot be converted.

    ``int()`` semantics are preserved (numeric strings convert, bools become
    0/1, floats truncate); unlike ``int``, a malformed value is not an error.
    """

    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and convert aware ones to UTC."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def datetime_to_epoch_ms(value: datetime | None) -> int | None:
    """Convert a datetime to epoch milliseconds (naive values are UTC)."""

    if value is None:
        return None
    return int(as_utc(value).timestamp() * 1000)


def collapse_whitespace(value: str) -> str:
    """Collapse all whitespace runs to single spaces and trim the ends."""

    return " ".join(value.split())


def compact_text(value: str, max_length: int | None = None) -> str:
    """Collapse whitespace runs, trim, and optionally truncate to ``max_length``.

    Model output arrives with arbitrary newlines and padding; callers that also
    need a hard cap pass ``max_length`` instead of re-slicing afterwards.
    """

    text = collapse_whitespace(value)
    return text[:max_length] if max_length is not None else text


def is_blank(value: object) -> bool:
    """Return whether a value carries no textual content.

    ``None``, an empty or whitespace-only string, an empty list, and a list
    whose elements are all blank strings are all considered blank.
    """

    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list):
        if not value:
            return True
        return all(isinstance(item, str) and item.strip() == "" for item in value)
    return False


def to_jsonable(value: Any) -> Any:
    """Recursively convert driver values into JSON-serializable ones.

    Neo4j temporal values expose ``to_native()``; datetimes become ISO-8601
    with a ``Z`` suffix; dicts/lists/tuples are converted recursively; other
    non-primitive values fall back to ``str``.
    """

    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, datetime):
        return as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
