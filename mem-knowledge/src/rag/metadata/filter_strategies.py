"""SQLAlchemy metadata filter strategies copied from the legacy API."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import DateTime, Numeric, and_, cast, func, literal, or_

from ...errors import KnowledgeError
from ...models.owned import Document
from ...utils.datetime_utils import parse_metadata_time_to_utc_naive


def _invalid(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_VALIDATION_ERROR", message)


def _escape_like(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FilterStrategy(ABC):
    @property
    @abstractmethod
    def supported_operators(self) -> list[str]: ...

    @abstractmethod
    def apply(self, field_name: str, operator: str, value: Any) -> Any: ...

    def supports(self, operator: str) -> bool:
        return operator in self.supported_operators


class StringFilterStrategy(FilterStrategy):
    supported_operators = [
        "eq",
        "ne",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        "is_empty",
        "not_empty",
        "in",
        "not_in",
    ]

    def apply(self, field_name: str, operator: str, value: Any) -> Any:
        col = Document.meta_data[field_name].astext
        values = (
            list(value) if hasattr(value, "__iter__") and not isinstance(value, str) else [value]
        )
        match operator:
            case "eq":
                return col == str(value)
            case "ne":
                return col != str(value)
            case "contains":
                return col.like(f"%{_escape_like(value)}%", escape="\\")
            case "not_contains":
                return ~col.like(f"%{_escape_like(value)}%", escape="\\")
            case "starts_with":
                return col.like(f"{_escape_like(value)}%", escape="\\")
            case "ends_with":
                return col.like(f"%{_escape_like(value)}", escape="\\")
            case "is_empty":
                return or_(col.is_(None), col == "")
            case "not_empty":
                return and_(col.is_not(None), col != "")
            case "in":
                return col.in_([str(item) for item in values])
            case "not_in":
                return ~col.in_([str(item) for item in values])
        raise _invalid(f"Unsupported metadata operator: {operator}")


class NumberFilterStrategy(FilterStrategy):
    supported_operators = ["eq", "ne", "gt", "lt", "gte", "lte", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any) -> Any:
        raw = Document.meta_data[field_name].astext
        col = cast(raw, Numeric)
        number = float(value) if operator not in {"is_empty", "not_empty"} else None
        match operator:
            case "eq":
                return col == number
            case "ne":
                return col != number
            case "gt":
                return col > number
            case "lt":
                return col < number
            case "gte":
                return col >= number
            case "lte":
                return col <= number
            case "is_empty":
                return raw.is_(None)
            case "not_empty":
                return raw.is_not(None)
        raise _invalid(f"Unsupported metadata operator: {operator}")


class TimeFilterStrategy(FilterStrategy):
    supported_operators = ["eq", "before", "after", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any) -> Any:
        raw = Document.meta_data[field_name].astext
        col = func.timezone("UTC", cast(raw, DateTime(timezone=True)))
        parsed = None
        if operator in {"eq", "before", "after"}:
            parsed = parse_metadata_time_to_utc_naive(value)
            if parsed is None:
                raise _invalid("Invalid metadata time value")
        value_expr = literal(parsed, DateTime) if parsed else None
        match operator:
            case "eq":
                return func.date_trunc("minute", col) == func.date_trunc("minute", value_expr)
            case "before":
                return func.date_trunc("minute", col) < func.date_trunc("minute", value_expr)
            case "after":
                return func.date_trunc("minute", col) > func.date_trunc("minute", value_expr)
            case "is_empty":
                return raw.is_(None)
            case "not_empty":
                return raw.is_not(None)
        raise _invalid(f"Unsupported metadata operator: {operator}")


__all__ = ["NumberFilterStrategy", "StringFilterStrategy", "TimeFilterStrategy", "_escape_like"]
