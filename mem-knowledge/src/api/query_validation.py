"""Validate legacy list filters that otherwise fail in the database layer."""

from uuid import UUID

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import ColumnProperty, InstrumentedAttribute
from sqlalchemy.sql import ColumnElement

from ..errors import KnowledgeError


def parse_uuid_filter(value: str | None, *, field: str) -> list[UUID] | None:
    if not value:
        return None
    try:
        ids = []
        for item in value.split(","):
            # Keep the accepted shape of the PostgreSQL asyncpg UUID encoder.
            if not 32 <= len(item) <= 36 or not all(c in "0123456789abcdefABCDEF-" for c in item):
                raise ValueError("invalid UUID")
            ids.append(UUID(item))
        return ids
    except ValueError as exc:
        raise KnowledgeError.from_code(
            "KB_HTTP_VALIDATION_ERROR", diagnostic=f"query.{field}: invalid UUID list"
        ) from exc


def list_order_clause(
    model: type, value: str | None, *, desc: bool
) -> ColumnElement | None:
    # Unknown names historically mean no explicit sort.
    attribute = getattr(model, value, None) if value else None
    if attribute is None:
        return None
    if (
        not isinstance(attribute, InstrumentedAttribute)
        or not isinstance(attribute.property, ColumnProperty)
        or (isinstance(attribute.type, JSON) and not isinstance(attribute.type, JSONB))
    ):
        raise KnowledgeError.from_code(
            "KB_HTTP_VALIDATION_ERROR", diagnostic="query.orderby: field cannot be sorted"
        )
    return attribute.desc() if desc else attribute.asc()
