import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.expression_evaluator import evaluate_expression
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.cycle_graph import LoopNodeConfig
from app.core.workflow.nodes.enums import ValueInputType, ComparisonOperator, LogicOperator
from app.core.workflow.nodes.operators import TypeTransformer, ConditionExpressionResolver, CompareOperatorInstance
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class LoopRuntime:
    """
    Runtime executor for a loop node in a workflow graph.

    This class is responsible for executing a loop node at runtime:
    - Initializing loop-scoped variables
    - Evaluating loop continuation conditions
    - Repeatedly invoking a compiled sub-graph
    - Enforcing maximum loop count and external stop signals
    """

    def __init__(
            self,
            graph: CompiledStateGraph,
            node_id: str,
            config: dict[str, Any],
            state: WorkflowState,
    ):
        """
        Initialize the loop runtime executor.

        Args:
            graph: A compiled LangGraph state graph representing the loop body.
            node_id: The unique identifier of the loop node in the workflow.
            config: Raw configuration dictionary for the loop node.
            state: The current workflow state before entering the loop.
        """
        self.graph = graph
        self.state = state
        self.node_id = node_id
        self.typed_config = LoopNodeConfig(**config)

    def _init_loop_state(self):
        """
        Initialize workflow state for loop execution.

        This method:
        - Evaluates initial values of loop variables
        - Stores loop variables into both `runtime_vars` and `node_outputs`
          under the current loop node's scope
        - Creates a shallow copy of the workflow state
        - Marks the loop as active by setting `looping = True`

        Returns:
            WorkflowState: A prepared workflow state used for loop execution.
        """
        pool = VariablePool(self.state)
        # 循环变量
        self.state["runtime_vars"][self.node_id] = {
            variable.name: evaluate_expression(
                expression=variable.value,
                variables=pool.get_all_conversation_vars(),
                node_outputs=pool.get_all_node_outputs(),
                system_vars=pool.get_all_system_vars(),
            ) if variable.input_type == ValueInputType.VARIABLE else TypeTransformer.transform(variable.value, variable.type)
            for variable in self.typed_config.cycle_vars
        }
        self.state["node_outputs"][self.node_id] = {
            variable.name: evaluate_expression(
                expression=variable.value,
                variables=pool.get_all_conversation_vars(),
                node_outputs=pool.get_all_node_outputs(),
                system_vars=pool.get_all_system_vars(),
            ) if variable.input_type == ValueInputType.VARIABLE else TypeTransformer.transform(variable.value, variable.type)
            for variable in self.typed_config.cycle_vars
        }
        loopstate = WorkflowState(
            **self.state
        )
        loopstate["looping"] = True
        return loopstate

    @staticmethod
    def _evaluate(operator, instance: CompareOperatorInstance) -> Any:
        """
        Dispatch and execute a comparison operator against a resolved
        CompareOperatorInstance.

        Args:
            operator: A ComparisonOperator enum value.
            instance: A CompareOperatorInstance bound to concrete operands.

        Returns:
            Any: The evaluation result, typically a boolean.
        """
        match operator:
            case ComparisonOperator.EMPTY:
                return instance.empty()
            case ComparisonOperator.NOT_EMPTY:
                return instance.not_empty()
            case ComparisonOperator.CONTAINS:
                return instance.contains()
            case ComparisonOperator.NOT_CONTAINS:
                return instance.not_contains()
            case ComparisonOperator.START_WITH:
                return instance.startswith()
            case ComparisonOperator.END_WITH:
                return instance.endswith()
            case ComparisonOperator.EQ:
                return instance.eq()
            case ComparisonOperator.NE:
                return instance.ne()
            case ComparisonOperator.LT:
                return instance.lt()
            case ComparisonOperator.LE:
                return instance.le()
            case ComparisonOperator.GT:
                return instance.gt()
            case ComparisonOperator.GE:
                return instance.ge()
            case _:
                raise ValueError(f"Invalid condition: {operator}")

    def evaluate_conditional(self, state) -> bool:
        """
        Evaluate the loop continuation condition at runtime.

        This method:
        - Resolves all condition expressions against the current workflow state
        - Evaluates each comparison expression immediately
        - Combines results using the configured logical operator (AND / OR)

        Args:
            state: The current workflow state during loop execution.

        Returns:
            bool: True if the loop should continue, False otherwise.
        """
        conditions = []

        for expression in self.typed_config.condition.expressions:
            left_value = VariablePool(state).get(expression.left)
            evaluator = ConditionExpressionResolver.resolve_by_value(left_value)(
                VariablePool(state),
                expression.left,
                expression.right,
                expression.input_type
            )
            conditions.append(self._evaluate(expression.operator, evaluator))
        if self.typed_config.condition.logical_operator == LogicOperator.AND:
            return all(conditions)
        else:
            return any(conditions)

    async def run(self):
        """
        Execute the loop node until termination conditions are met.

        The loop terminates when any of the following occurs:
        - The loop condition evaluates to False
        - The `looping` flag in the workflow state is set to False
        - The maximum loop count is reached

        Returns:
            dict[str, Any]: The final runtime variables of this loop node.
        """
        loopstate = self._init_loop_state()
        loop_time = self.typed_config.max_loop
        while self.evaluate_conditional(loopstate) and loopstate["looping"] and loop_time > 0:
            logger.info(f"loop node {self.node_id}: running")
            await self.graph.ainvoke(loopstate)
            loop_time -= 1

        logger.info(f"loop node {self.node_id}: execution completed")
        return loopstate["runtime_vars"][self.node_id]
