import logging
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.enums import ComparisonOperator
from app.core.workflow.nodes.list_operator.config import ListOperatorNodeConfig, FilterCondition
from app.core.workflow.variable.base_variable import VariableType

logger = logging.getLogger(__name__)

# File object fields that hold string values
_FILE_STRING_KEYS = {"name", "extension", "mime_type", "url", "transfer_method", "origin_file_type", "file_id"}
_FILE_NUMBER_KEYS = {"size"}


class ListOperatorNode(BaseNode):
    def __init__(self, node_config: dict, workflow_config: dict, down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: ListOperatorNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "result": VariableType.ANY,
            "first_record": VariableType.ANY,
            "last_record": VariableType.ANY,
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        self.typed_config = ListOperatorNodeConfig(**self.config)
        cfg = self.typed_config

        # Resolve input variable from path selector
        items: list = self.get_variable(cfg.input_list, variable_pool)
        if not isinstance(items, list):
            raise TypeError(f"Variable '{cfg.input_list}' must be an array, got {type(items)}")

        result = list(items)

        # 1. Filter
        if cfg.filter_by.enabled and cfg.filter_by.conditions:
            for condition in cfg.filter_by.conditions:
                result = [item for item in result if self._match_condition(item, condition, variable_pool)]

        # 2. Extract (take single item by 1-based serial index)
        if cfg.extract_by.enabled:
            serial_str = self._resolve_value(cfg.extract_by.serial, variable_pool)
            idx = int(serial_str) - 1
            if idx < 0 or idx >= len(result):
                raise ValueError(f"extract_by.serial={cfg.extract_by.serial} out of range (list length={len(result)})")
            result = [result[idx]]

        # 3. Order
        if cfg.order_by.enabled and cfg.order_by.key:
            reverse = cfg.order_by.value == "desc"
            key_fn = self._make_sort_key(cfg.order_by.key)
            result = sorted(result, key=key_fn, reverse=reverse)

        # 4. Limit (take first N)
        if cfg.limit.enabled and cfg.limit.size > 0:
            result = result[:cfg.limit.size]

        return {
            "result": result,
            "first_record": result[0] if result else None,
            "last_record": result[-1] if result else None,
        }

    @staticmethod
    def _resolve_value(value: str, variable_pool: VariablePool) -> Any:
        """If value is a {{ namespace.key }} variable selector, resolve it from the pool.
        Otherwise return the raw string."""
        import re
        m = re.fullmatch(r"\{\{\s*(\w+\.\w+)\s*}}", value.strip())
        if m:
            resolved = variable_pool.get_value(value, default=value, strict=False)
            return resolved
        return value

    @staticmethod
    def _make_sort_key(key: str):
        def key_fn(item):
            if isinstance(item, dict):
                return item.get(key) or ""
            return item
        return key_fn

    def _match_condition(self, item: Any, cond: FilterCondition, variable_pool: VariablePool) -> bool:
        op = cond.comparison_operator
        value = cond.value

        # Resolve value if it's a variable reference {{ namespace.key }}
        if isinstance(value, str):
            value = self._resolve_value(value, variable_pool)

        # Resolve left value
        if isinstance(item, dict):
            left = item.get(cond.key)
        else:
            left = item  # primitive array: compare element directly

        # Numeric operators
        if op == ComparisonOperator.EQ:
            return self._safe_num(left) == self._safe_num(value)
        if op == ComparisonOperator.NE:
            return self._safe_num(left) != self._safe_num(value)
        if op == ComparisonOperator.LT:
            return self._safe_num(left) < self._safe_num(value)
        if op == ComparisonOperator.LE:
            return self._safe_num(left) <= self._safe_num(value)
        if op == ComparisonOperator.GT:
            return self._safe_num(left) > self._safe_num(value)
        if op == ComparisonOperator.GE:
            return self._safe_num(left) >= self._safe_num(value)

        # String / sequence operators
        left_str = str(left) if left is not None else ""
        if op == ComparisonOperator.CONTAINS:
            return str(value) in left_str
        if op == ComparisonOperator.NOT_CONTAINS:
            return str(value) not in left_str
        if op == ComparisonOperator.START_WITH:
            return left_str.startswith(str(value))
        if op == ComparisonOperator.END_WITH:
            return left_str.endswith(str(value))
        if op == ComparisonOperator.IN:
            return left_str in (value if isinstance(value, list) else [str(value)])
        if op == ComparisonOperator.NOT_IN:
            return left_str not in (value if isinstance(value, list) else [str(value)])
        if op == ComparisonOperator.EMPTY:
            return not left
        if op == ComparisonOperator.NOT_EMPTY:
            return bool(left)

        raise ValueError(f"Unsupported operator: {op}")

    @staticmethod
    def _safe_num(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
