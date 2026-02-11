import logging
import re
from typing import Any

from app.core.workflow.nodes.assigner.config import AssignerNodeConfig
from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.enums import AssignmentOperator
from app.core.workflow.nodes.operators import AssignmentOperatorInstance, AssignmentOperatorResolver
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class AssignerNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.variable_updater = True
        self.typed_config: AssignerNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {}

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """
        Execute the assignment operation defined by this node.

        Args:
            state: The current workflow state, including conversation variables,
                   node outputs, and system variables.
            variable_pool: variable pool

        Returns:
            None or the result of the assignment operation.
        """
        # Initialize a variable pool for accessing conversation, node, and system variables
        self.typed_config = AssignerNodeConfig(**self.config)
        logger.info(f"节点 {self.node_id} 开始执行")
        pattern = r"\{\{\s*(.*?)\s*\}\}"

        for assignment in self.typed_config.assignments:
            # Get the target variable selector (e.g., "conv.test")
            variable_selector = assignment.variable_selector
            namespace = re.sub(pattern, r"\1", variable_selector).split('.')[0]

            # Only conversation variables ('conv') are allowed
            if namespace != 'conv' and namespace not in state["cycle_nodes"]:
                raise ValueError(f"Only conversation or cycle variables can be assigned. - {variable_selector}")

            # Get the value or expression to assign
            value = assignment.value
            logger.debug(f"left:{variable_selector}, right: {value}")

            if isinstance(value, str):
                expression = re.match(pattern, value)
                if expression:
                    expression = expression.group(1)
                    expression = re.sub(pattern, r"\1", expression).strip()
                    value = self.get_variable(expression, variable_pool, default=value, strict=False)

            # Select the appropriate assignment operator instance based on the target variable type
            operator: AssignmentOperatorInstance = AssignmentOperatorResolver.resolve_by_value(
                variable_pool.get_value(variable_selector)
            )(
                variable_pool, variable_selector, value
            )

            # Execute the configured assignment operation
            match assignment.operation:
                case AssignmentOperator.COVER:
                    await operator.assign()
                case AssignmentOperator.ASSIGN:
                    await operator.assign()
                case AssignmentOperator.CLEAR:
                    await operator.clear()
                case AssignmentOperator.ADD:
                    await operator.add()
                case AssignmentOperator.SUBTRACT:
                    await operator.subtract()
                case AssignmentOperator.MULTIPLY:
                    await operator.multiply()
                case AssignmentOperator.DIVIDE:
                    await operator.divide()
                case AssignmentOperator.APPEND:
                    await operator.append()
                case AssignmentOperator.REMOVE_FIRST:
                    await operator.remove_first()
                case AssignmentOperator.REMOVE_LAST:
                    await operator.remove_last()
                case _:
                    raise ValueError(f"Invalid Operator: {assignment.operation}")
            logger.info(f"Node {self.node_id}: execution completed")
