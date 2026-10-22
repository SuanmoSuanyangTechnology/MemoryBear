import logging
from abc import ABC, abstractmethod
from typing import Any
from sqlalchemy import or_, and_, cast, func, DateTime, Numeric, String
from ...errors import BusinessException
from ...errors import BizCode
from ...utils.datetime_utils import parse_metadata_time_to_utc_naive
from ...models.document_model import Document

logger = logging.getLogger(__name__)


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards: % _ \\"""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FilterStrategy(ABC):
    """Metadata filter strategy interface"""

    @property
    @abstractmethod
    def supported_operators(self) -> list[str]:
        pass

    @abstractmethod
    def apply(self, field_name: str, operator: str, value: Any) -> Any:
        """Return the SQLAlchemy filter expression"""
        pass

    def supports(self, operator: str) -> bool:
        return operator in self.supported_operators


class StringFilterStrategy(FilterStrategy):
    """String-type filter strategy"""

    supported_operators = [
        "eq", "ne", "contains", "not_contains",
        "starts_with", "ends_with", "is_empty", "not_empty",
        "in", "not_in",
    ]

    def apply(self, field_name: str, operator: str, value: Any):
        col = Document.meta_data[field_name].astext

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
                values = list(value) if hasattr(value, '__iter__') and not isinstance(value, str) else [value]
                return col.in_([str(v) for v in values])
            case "not_in":
                values = list(value) if hasattr(value, '__iter__') and not isinstance(value, str) else [value]
                return ~col.in_([str(v) for v in values])

        raise BusinessException(
            f"StringFilterStrategy: unsupported operator '{operator}'",
            code=BizCode.METADATA_INVALID_OPERATOR,
        )


class NumberFilterStrategy(FilterStrategy):
    """Number-type filter strategy (supports integers and decimals)"""

    supported_operators = ["eq", "ne", "gt", "lt", "gte", "lte", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any):
        col = cast(Document.meta_data[field_name].astext, Numeric)

        def _num(v):
            f = float(v)
            return int(f) if f.is_integer() else f

        match operator:
            case "eq":
                return col == _num(value)
            case "ne":
                return col != _num(value)
            case "gt":
                return col > _num(value)
            case "lt":
                return col < _num(value)
            case "gte":
                return col >= _num(value)
            case "lte":
                return col <= _num(value)
            case "is_empty":
                return Document.meta_data[field_name].astext.is_(None)
            case "not_empty":
                return Document.meta_data[field_name].astext.is_not(None)

        raise BusinessException(
            f"NumberFilterStrategy: unsupported operator '{operator}'",
            code=BizCode.METADATA_INVALID_OPERATOR,
        )


class TimeFilterStrategy(FilterStrategy):
    """Time-type filter strategy (format: YYYY-MM-DD HH:MM)"""

    supported_operators = ["eq", "before", "after", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any):
        from sqlalchemy import literal
        col_text = Document.meta_data[field_name].astext
        col = func.timezone("UTC", cast(col_text, DateTime(timezone=True)))
        dt = None
        if operator in ("eq", "before", "after"):
            try:
                dt = parse_metadata_time_to_utc_naive(value)
                if dt is None:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise BusinessException(
                    "时间字段过滤参数必须为 ISO 时间格式或 Unix 时间戳，例如: 2024-12-31T23:59:59+08:00",
                    code=BizCode.METADATA_INVALID_VALUE_TYPE,
                ) from exc
            logger.debug(
                "[MetadataFilterStrategy] normalized custom time filter: "
                "field=%s operator=%s raw_value=%r raw_type=%s utc_naive=%s",
                field_name,
                operator,
                value,
                type(value).__name__,
                dt.isoformat(sep=" "),
            )
        dt_lit = literal(dt, DateTime) if dt else None

        match operator:
            case "eq":
                return func.date_trunc('minute', col) == func.date_trunc('minute', dt_lit)
            case "before":
                return func.date_trunc('minute', col) < func.date_trunc('minute', dt_lit)
            case "after":
                return func.date_trunc('minute', col) > func.date_trunc('minute', dt_lit)
            case "is_empty":
                return Document.meta_data[field_name].astext.is_(None)
            case "not_empty":
                return Document.meta_data[field_name].astext.is_not(None)

        raise BusinessException(
            f"TimeFilterStrategy: unsupported operator '{operator}'",
            code=BizCode.METADATA_INVALID_OPERATOR,
        )
