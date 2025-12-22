import logging
from typing import Any

from app.core.workflow.expression_evaluator import ExpressionEvaluator
from app.core.workflow.nodes.assigner.config import AssignerNodeConfig
from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.enums import AssignmentOperator
from app.core.workflow.nodes.operators import AssignmentOperatorInstance
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class AssignerNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = AssignerNodeConfig(**self.config)

    async def execute(self, state: WorkflowState) -> Any:
        """
        Execute the assignment operation defined by this node.

        Args:
            state: The current workflow state, including conversation variables,
                   node outputs, and system variables.

        Returns:
            None or the result of the assignment operation.
        """
        # Initialize a variable pool for accessing conversation, node, and system variables
        pool = VariablePool(state)

        # Get the target variable selector (e.g., "conv.test")
        variable_selector = self.typed_config.variable_selector
        if isinstance(variable_selector, str):
            # Support dot-separated string paths, e.g., "conv.test" -> ["conv", "test"]
            variable_selector = variable_selector.split('.')

        # Only conversation variables ('conv') are allowed
        if variable_selector[0] != 'conv':  # TODO: Loop node variable support (Feature)
            raise ValueError("Only conversation variables can be assigned.")

        # Get the value or expression to assign
        value = self.typed_config.value
        if isinstance(value, list):
            value = '.'.join(value)
        value = ExpressionEvaluator.evaluate(
            expression=value,
            variables=pool.get_all_conversation_vars(),
            node_outputs=pool.get_all_node_outputs(),
            system_vars=pool.get_all_system_vars(),
        )

        # Select the appropriate assignment operator instance based on the target variable type
        operator: AssignmentOperatorInstance = AssignmentOperator.get_operator(pool.get(variable_selector))(
            pool, variable_selector, value
        )

        # Execute the configured assignment operation
        match self.typed_config.operation:
            case AssignmentOperator.ASSIGN:
                operator.assign()
            case AssignmentOperator.CLEAR:
                operator.clear()
            case AssignmentOperator.ADD:
                operator.add()
            case AssignmentOperator.SUBTRACT:
                operator.subtract()
            case AssignmentOperator.MULTIPLY:
                operator.multiply()
            case AssignmentOperator.DIVIDE:
                operator.divide()
            case AssignmentOperator.APPEND:
                operator.append()
            case AssignmentOperator.REMOVE_FIRST:
                operator.remove_first()
            case AssignmentOperator.REMOVE_LAST:
                operator.remove_last()
            case _:
                raise ValueError(f"Invalid Operator: {self.typed_config.operation}")
