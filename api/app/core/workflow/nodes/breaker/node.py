import logging
from typing import Any

from app.core.workflow.nodes import BaseNode, WorkflowState
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class BreakNode(BaseNode):
    """
    Workflow node that immediately stops loop execution.

    When executed, this node sets the 'looping' flag in the workflow state
    to False, signaling the outer loop runtime to terminate further iterations.
    """

    def _output_types(self) -> dict[str, VariableType]:
        return {}

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """
        Execute the break node.

        Args:
            state: Current workflow state, including loop control flags.
            variable_pool: Pool of variables for the workflow.

        Effects:
            - Sets 'looping' in the state too False to stop the loop.
            - Logs the action for debugging purposes.

        Returns:
            Optional dictionary indicating the loop has been stopped.
        """
        state["looping"] = 2
        logger.info(f"Setting cycle node exit flag, cycle={self.cycle}, looping={state['looping']}")

