"""
Output node implementation.

The workflow's output node (similar to Dify workflow's end node); declares the
workflow's final output variables and does not produce streaming output.
"""

import logging
from typing import Any

from ...engine.state_manager import WorkflowState
from ...engine.variable_pool import VariablePool
from ..base_node import BaseNode
from ...variable.base_variable import VariableType

logger = logging.getLogger(__name__)


class OutputNode(BaseNode):
    """
    Output node.

    The workflow's output node; collects and outputs the values of the specified
    variables.
    """

    def _output_types(self) -> dict[str, VariableType]:
        outputs = self.config.get("outputs", [])
        return {
            item["name"]: VariableType(item.get("type", VariableType.STRING))
            for item in outputs if item.get("name")
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        outputs = self.config.get("outputs", [])
        result = {}
        for item in outputs:
            name = item.get("name")
            if not name:
                continue
            var_type = VariableType(item.get("type", VariableType.STRING))
            value = item.get("value", "")
            if var_type == VariableType.STRING:
                result[name] = self._render_template(str(value), variable_pool, strict=False)
            elif isinstance(value, str) and value.strip().startswith("{{") and value.strip().endswith("}}"):
                selector = value.strip()[2:-2].strip()
                result[name] = variable_pool.get_value(selector, default=None, strict=False)
            else:
                result[name] = value
        return result
