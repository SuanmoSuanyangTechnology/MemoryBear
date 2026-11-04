"""Shared validation helpers for profile transport inputs."""

from __future__ import annotations

from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError


def parse_required_uuid(value: str, *, name: str = "end_user_id") -> UUID:
    """Parse a required UUID and return the standard invalid-parameter error."""

    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise LocalizedProfileError(
            BizCode.INVALID_PARAMETER,
            "common.validation.invalid_parameter",
            message_params={"name": name},
            detail_key="common.validation.invalid_uuid",
            detail_params={"value": value},
            status_code=400,
        ) from exc


def parse_optional_uuid(value: str | None, *, name: str = "end_user_id") -> UUID | None:
    """Parse an optional UUID, treating blank strings as omitted."""

    if value is None or not value.strip():
        return None
    normalized = value.strip()
    return parse_required_uuid(normalized, name=name)
