from abc import ABC, abstractmethod
from typing import Any
from sqlalchemy import text, or_, and_


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
        json_path = f"meta_data->>'{field_name}'"

        match operator:
            case "eq":
                return text(f"{json_path} = :val").bindparams(val=str(value))
            case "ne":
                return text(f"{json_path} != :val").bindparams(val=str(value))
            case "contains":
                return text(f"{json_path} LIKE :val").bindparams(val=f"%{value}%")
            case "not_contains":
                return text(f"{json_path} NOT LIKE :val").bindparams(val=f"%{value}%")
            case "starts_with":
                return text(f"{json_path} LIKE :val").bindparams(val=f"{value}%")
            case "ends_with":
                return text(f"{json_path} LIKE :val").bindparams(val=f"%{value}")
            case "is_empty":
                return or_(
                    text(f"{json_path} IS NULL"),
                    text(f"{json_path} = ''"),
                )
            case "not_empty":
                return and_(
                    text(f"{json_path} IS NOT NULL"),
                    text(f"{json_path} != ''"),
                )
            case "in":
                values = list(value) if hasattr(value, '__iter__') and not isinstance(value, str) else [value]
                return text(f"{json_path} = ANY(:vals)").bindparams(vals=[str(v) for v in values])
            case "not_in":
                values = list(value) if hasattr(value, '__iter__') and not isinstance(value, str) else [value]
                return text(f"{json_path} != ALL(:vals)").bindparams(vals=[str(v) for v in values])

        raise ValueError(f"StringFilterStrategy: unsupported operator '{operator}'")


class NumberFilterStrategy(FilterStrategy):
    """数字类型过滤策略（支持整数和小数）"""

    supported_operators = ["eq", "ne", "gt", "lt", "gte", "lte", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any):
        json_path = f"(meta_data->>'{field_name}')::numeric"

        def _num(v):
            f = float(v)
            return int(f) if f.is_integer() else f

        match operator:
            case "eq":
                return text(f"{json_path} = :val").bindparams(val=_num(value))
            case "ne":
                return text(f"{json_path} != :val").bindparams(val=_num(value))
            case "gt":
                return text(f"{json_path} > :val").bindparams(val=_num(value))
            case "lt":
                return text(f"{json_path} < :val").bindparams(val=_num(value))
            case "gte":
                return text(f"{json_path} >= :val").bindparams(val=_num(value))
            case "lte":
                return text(f"{json_path} <= :val").bindparams(val=_num(value))
            case "is_empty":
                return text(f"(meta_data->>'{field_name}') IS NULL")
            case "not_empty":
                return text(f"(meta_data->>'{field_name}') IS NOT NULL")

        raise ValueError(f"NumberFilterStrategy: unsupported operator '{operator}'")


class TimeFilterStrategy(FilterStrategy):
    """时间类型过滤策略（格式: YYYY-MM-DD HH:MM）"""

    supported_operators = ["eq", "before", "after", "is_empty", "not_empty"]

    def apply(self, field_name: str, operator: str, value: Any):
        json_path = f"(meta_data->>'{field_name}')::timestamp"

        match operator:
            case "eq":
                return text(f"{json_path} = :val").bindparams(val=str(value))
            case "before":
                return text(f"{json_path} < :val").bindparams(val=str(value))
            case "after":
                return text(f"{json_path} > :val").bindparams(val=str(value))
            case "is_empty":
                return text(f"(meta_data->>'{field_name}') IS NULL")
            case "not_empty":
                return text(f"(meta_data->>'{field_name}') IS NOT NULL")

        raise ValueError(f"TimeFilterStrategy: unsupported operator '{operator}'")
