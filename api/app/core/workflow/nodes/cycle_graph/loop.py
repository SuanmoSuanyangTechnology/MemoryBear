from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.expression_evaluator import evaluate_condition, evaluate_expression
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.cycle_graph import LoopNodeConfig
from app.core.workflow.nodes.operators import ConditionExpressionBuilder
from app.core.workflow.variable_pool import VariablePool


class LoopRuntime:
    """
    Runtime executor for loop nodes in a workflow.

    Handles iterative execution of a loop node according to defined loop variables
    and conditional expressions. Supports maximum loop count and loop control
    through the workflow state.
    """

    def __init__(
            self,
            graph: CompiledStateGraph,
            node_id: str,
            config: dict[str, Any],
            state: WorkflowState,
    ):
        """
        Initialize the loop runtime.

        Args:
            graph: Compiled workflow graph capable of async invocation.
            node_id: Unique identifier of the loop node.
            config: Dictionary containing loop node configuration.
            state: Current workflow state at the point of loop execution.
        """
        self.graph = graph
        self.state = state
        self.node_id = node_id
        self.typed_config = LoopNodeConfig(**config)

    def _init_loop_state(self):
        """
        Initialize workflow state for loop execution.

        - Evaluates initial values of loop variables.
        - Stores loop variables in runtime_vars and node_outputs.
        - Marks the loop as active by setting 'looping' to True.

        Returns:
            A copy of the workflow state prepared for the loop execution.
        """
        pool = VariablePool(self.state)
        # 循环变量
        self.state["runtime_vars"][self.node_id] = {
            variable.name: evaluate_expression(
                expression=variable.value,
                variables=pool.get_all_conversation_vars(),
                node_outputs=pool.get_all_node_outputs(),
                system_vars=pool.get_all_system_vars(),
            )
            for variable in self.typed_config.cycle_vars
        }
        self.state["node_outputs"][self.node_id] = {
            variable.name: evaluate_expression(
                expression=variable.value,
                variables=pool.get_all_conversation_vars(),
                node_outputs=pool.get_all_node_outputs(),
                system_vars=pool.get_all_system_vars(),
            )
            for variable in self.typed_config.cycle_vars
        }
        loopstate = WorkflowState(
            **self.state
        )
        loopstate["looping"] = True
        return loopstate

    def _get_loop_expression(self):
        """
        Build the Python boolean expression for evaluating the loop condition.

        - Converts each condition in the loop configuration into a Python expression string.
        - Combines multiple conditions with the configured logical operator (AND/OR).

        Returns:
            A string representing the combined loop condition expression.
        """
        branch_conditions = [
            ConditionExpressionBuilder(
                left=condition.left,
                operator=condition.comparison_operator,
                right=condition.right
            ).build()
            for condition in self.typed_config.condition.expressions
        ]
        if len(branch_conditions) > 1:
            combined_condition = f' {self.typed_config.condition.logical_operator} '.join(branch_conditions)
        else:
            combined_condition = branch_conditions[0]

        return combined_condition

    async def run(self):
        """
        Execute the loop node until the condition is no longer met, the loop is
        manually stopped, or the maximum loop count is reached.

        Returns:
            The final runtime variables of this loop node after completion.
        """
        loopstate = self._init_loop_state()
        expression = self._get_loop_expression()
        loop_variable_pool = VariablePool(loopstate)
        loop_time = self.typed_config.max_loop
        while evaluate_condition(
                expression=expression,
                variables=loop_variable_pool.get_all_conversation_vars(),
                node_outputs=loop_variable_pool.get_all_node_outputs(),
                system_vars=loop_variable_pool.get_all_system_vars(),
        ) and loopstate["looping"] and loop_time > 0:
            await self.graph.ainvoke(loopstate)
            loop_time -= 1
        return loopstate["runtime_vars"][self.node_id]
