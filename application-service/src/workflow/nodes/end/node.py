"""
End node implementation.

The workflow's end node, which outputs the final result.
"""

import logging

from ...engine.state_manager import WorkflowState
from ...engine.variable_pool import VariablePool
from ..base_node import BaseNode
from ...variable.base_variable import VariableType

logger = logging.getLogger(__name__)


class EndNode(BaseNode):
    """End node.

    The workflow's end node; outputs the final result based on the configured
    template. Supports real-time streaming output: when the template references
    an upstream node's output, its streaming buffer is monitored live.
    """
    def _output_types(self) -> dict[str, VariableType]:
        """Declare this node's output types."""
        return {
            "output": VariableType.STRING
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> str:
        """Execute the end node business logic.

        Args:
            state: Workflow state.
            variable_pool: Variable pool.

        Returns:
            The final output string.
        """
        # Get the configured output template
        output_template = self.config.get("output")

        # If an output template is configured, render it; otherwise use the default output
        if output_template:
            output = self._render_template(output_template, variable_pool, strict=False)
        else:
            output = ""
        return output
