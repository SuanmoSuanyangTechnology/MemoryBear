"""Text normalization shared by the domain services."""

from __future__ import annotations


def collapse_whitespace(value: str) -> str:
    """Collapse all whitespace runs to single spaces and trim the ends."""

    return " ".join(value.split())


def compact_text(value: str, max_length: int | None = None) -> str:
    """Collapse whitespace runs, trim, and optionally truncate to ``max_length``."""

    text = collapse_whitespace(value)
    return text[:max_length] if max_length is not None else text
