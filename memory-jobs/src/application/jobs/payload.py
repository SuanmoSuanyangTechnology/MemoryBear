"""Payload helpers shared by the job handlers.

The executor decodes a message into a plain mapping. These helpers are the
boundary where a missing or mistyped field becomes a clear ``ValueError``
instead of a confusing failure deep inside a use case.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain.shared.ids import WorkspaceId, parse_uuid


def required_text(payload: Mapping[str, Any], key: str) -> str:
    """A required non-empty string field."""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def text(payload: Mapping[str, Any], key: str, default: str) -> str:
    """An optional string field; a non-string value falls back to ``default``."""

    value = payload.get(key)
    return value if isinstance(value, str) else default


def flag(payload: Mapping[str, Any], key: str, default: bool) -> bool:
    """An optional boolean field, keeping JSON's own ``true``/``false``."""

    value = payload.get(key, default)
    return value if isinstance(value, bool) else bool(value)


def integer(payload: Mapping[str, Any], key: str, default: int) -> int:
    """An optional integer field; a value that is not a number is an error."""

    value = payload.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer, got {value!r}") from None


def workspace_id_from(payload: Mapping[str, Any]) -> WorkspaceId:
    """The required workspace id; a malformed uuid fails here, not downstream."""

    raw = payload.get("workspace_id")
    parsed = parse_uuid(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise ValueError(f"invalid workspace_id: {raw!r}")
    return WorkspaceId(parsed)
