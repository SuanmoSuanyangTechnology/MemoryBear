"""Text sanitation helper (frozen port of the host
``app.core.utils.text_sanitize``).

PostgreSQL VARCHAR/TEXT/JSON columns reject NUL (``U+0000``) even though the
byte sequence is valid UTF-8; writes through asyncpg fail with
``CharacterNotInRepertoireError``. NUL typically arrives when a node decodes
binary content (ZIP/PDF) as text. Workflow output is sanitized before it
lands in the database or reaches the frontend.
"""

from __future__ import annotations

from typing import Any

# U+0000 is the only character PostgreSQL text types categorically reject;
# clean only it so that other control characters (tabs, color codes) survive.
_NUL_CHAR = "\x00"


def sanitize_text(value: str) -> str:
    """Strip NUL characters; returns the input unchanged (zero copy) when clean."""
    return value.replace(_NUL_CHAR, "") if _NUL_CHAR in value else value


def sanitize_value(value: Any) -> Any:
    """Recursively strip NUL characters from a JSON-compatible structure.

    Returns the original object when nothing changed (zero copy in the common
    case, avoiding a deep copy of large events); rebuilds only the affected
    dict/list containers. Non-string leaves (numbers, booleans, None, UUIDs,
    pydantic models) pass through unchanged.
    """
    cleaned, changed = _sanitize(value)
    return cleaned


def _sanitize(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        if _NUL_CHAR in value:
            return value.replace(_NUL_CHAR, ""), True
        return value, False
    if isinstance(value, dict):
        changed = False
        new_dict: dict | None = None
        for k, v in value.items():
            cleaned_v, item_changed = _sanitize(v)
            if item_changed:
                if new_dict is None:
                    new_dict = dict(value)
                new_dict[k] = cleaned_v
                changed = True
        return (new_dict if changed else value), changed
    if isinstance(value, list):
        changed = False
        new_list: list | None = None
        for i, v in enumerate(value):
            cleaned_v, item_changed = _sanitize(v)
            if item_changed:
                if new_list is None:
                    new_list = list(value)
                new_list[i] = cleaned_v
                changed = True
        return (new_list if changed else value), changed
    return value, False
