"""Display-text normalization (pure value shaping).

Lives in the domain layer because both the application projections and the
side-effect adapter shape display text; it carries no orchestration and no
external dependency.
"""

from __future__ import annotations

import re
from typing import Any

QUERY_MAX_LENGTH = 200
SUMMARY_MAX_LENGTH = 200
ENTITY_MAX_LENGTH = 100
MAX_SUMMARY_COUNT = 3
MAX_ENTITY_COUNT = 3

_TRUNCATE_SUFFIX = "…"
_FILE_SUMMARY_TAG_RE = re.compile(
    r"<input-file-summary>.*?</input-file-summary>",
    flags=re.DOTALL | re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_display_text(text: Any, max_length: int) -> str:
    """Collapse whitespace, strip, then truncate with an ellipsis suffix."""

    if not text:
        return ""
    normalized = _WHITESPACE_RE.sub(" ", str(text)).strip()
    if not normalized:
        return ""
    if len(normalized) > max_length:
        return normalized[:max_length] + _TRUNCATE_SUFFIX
    return normalized


def clean_query_for_display(processed_query: str) -> str:
    """Strip file-summary tags, normalize whitespace and truncate for display."""

    if not processed_query:
        return ""
    without_file_summary = _FILE_SUMMARY_TAG_RE.sub(" ", processed_query)
    return normalize_display_text(without_file_summary, QUERY_MAX_LENGTH)


__all__ = [
    "ENTITY_MAX_LENGTH",
    "MAX_ENTITY_COUNT",
    "MAX_SUMMARY_COUNT",
    "QUERY_MAX_LENGTH",
    "SUMMARY_MAX_LENGTH",
    "clean_query_for_display",
    "normalize_display_text",
]