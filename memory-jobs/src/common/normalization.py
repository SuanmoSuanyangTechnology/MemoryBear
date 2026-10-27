"""Dependency-free normalization helpers shared by the feature modules."""

from __future__ import annotations

from uuid import UUID

SUPPORTED_LANGUAGES = frozenset({"zh", "en"})


def normalize_language(value: str | None) -> str:
    """Return a supported primary language code; default ``zh``."""

    if not value:
        return "zh"
    first = value.split(",", 1)[0].split(";", 1)[0].strip().lower()
    primary = first.split("-", 1)[0]
    return primary if primary in SUPPORTED_LANGUAGES else "zh"


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


def collapse_whitespace(value: str) -> str:
    """Collapse all whitespace runs to single spaces and trim the ends."""

    return " ".join(value.split())


def compact_text(value: str, max_length: int | None = None) -> str:
    """Collapse whitespace runs, trim, and optionally truncate to ``max_length``."""

    text = collapse_whitespace(value)
    return text[:max_length] if max_length is not None else text
