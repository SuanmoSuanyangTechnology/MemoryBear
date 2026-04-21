"""
Output 节点实现

工作流的输出节点（类似 Dify workflow 的 end 节点），
用于定义工作流的最终输出变量，不产生流式输出。
"""

import logging
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.variable.base_variable import VariableType

logger = logging.getLogger(__name__)


class OutputNode(BaseNode):
    """
    Output 节点

    工作流的输出节点，收集并输出指定变量的值。
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
