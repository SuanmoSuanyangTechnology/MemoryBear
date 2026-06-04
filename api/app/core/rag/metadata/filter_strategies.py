from abc import ABC, abstractmethod
from typing import Any
from sqlalchemy import or_, and_, cast, func, DateTime, Numeric, String
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.core.utils.datetime_utils import parse_iso_to_utc_naive
from app.models.document_model import Document


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符：% _ \\"""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FilterStrategy(ABC):
    """元数据过滤策略接口"""

    @property
    @abstractmethod
    def supported_operators(self) -> list[str]:
        pass

    @abstractmethod
    def apply(self, field_name: str, operator: str, value: Any) -> Any:
        """返回 SQLAlchemy 过滤表达式"""
        pass

    def supports(self, operator: str) -> bool:
        return operator in self.supported_operators


class StringFilterStrategy(FilterStrategy):
    """字符串类型过滤策略"""

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
    """数字类型过滤策略（支持整数和小数）"""

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
    """时间类型过滤策略（格式: YYYY-MM-DD HH:MM）"""

    supported_operators = ["eq", "before", "after", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any):
        from sqlalchemy import literal
        col = cast(Document.meta_data[field_name].astext, DateTime)
        dt = None
        if operator in ("eq", "before", "after"):
            try:
                dt = parse_iso_to_utc_naive(str(value))
                if dt is None:
                    raise ValueError
            except ValueError as exc:
                raise BusinessException(
                    "时间字段过滤参数必须为 ISO 时间格式，例如: 2024-12-31T23:59:59",
                    code=BizCode.METADATA_INVALID_VALUE_TYPE,
                ) from exc
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
