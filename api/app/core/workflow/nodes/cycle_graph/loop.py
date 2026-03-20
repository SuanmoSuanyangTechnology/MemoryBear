import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.cycle_graph import LoopNodeConfig
from app.core.workflow.nodes.enums import ValueInputType, ComparisonOperator, LogicOperator, NodeType
from app.core.workflow.nodes.operators import TypeTransformer, ConditionExpressionResolver, CompareOperatorInstance
from app.core.workflow.utils.expression_evaluator import evaluate_expression

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
            start_id: str,
            stream: bool,
            graph: CompiledStateGraph,
            node_id: str,
            config: dict[str, Any],
            state: WorkflowState,
            variable_pool: VariablePool,
            child_variable_pool: VariablePool
    ):
        """
        Initialize the loop runtime executor.

        Args:
            graph: A compiled LangGraph state graph representing the loop body.
            node_id: The unique identifier of the loop node in the workflow.
            config: Raw configuration dictionary for the loop node.
            state: The current workflow state before entering the loop.
            variable_pool: A VariablePool instance for accessing and modifying workflow variables.
            child_variable_pool: A VariablePool instance for managing child node outputs.
        """
        self.start_id = start_id
        self.stream = stream
        self.graph = graph
        self.state = state
        self.node_id = node_id
        self.typed_config = LoopNodeConfig(**config)
        self.looping = True
        self.variable_pool = variable_pool
        self.child_variable_pool = child_variable_pool
        self.event_write = get_stream_writer()

        self.checkpoint = RunnableConfig(
            configurable={
                "thread_id": uuid.uuid4()
            }
        )

    async def _init_loop_state(self):
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
        # 循环变量
        self.child_variable_pool.copy(self.variable_pool)

        for variable in self.typed_config.cycle_vars:
            if variable.input_type == ValueInputType.VARIABLE:
                value = evaluate_expression(
                    expression=variable.value,
                    conv_var=self.variable_pool.get_all_conversation_vars(),
                    node_outputs=self.variable_pool.get_all_node_outputs(),
                    system_vars=self.variable_pool.get_all_system_vars(),
                )
            else:
                value = TypeTransformer.transform(variable.value, variable.type)
            await self.child_variable_pool.new(self.node_id, variable.name, value, variable.type, mut=True)
        loopstate = WorkflowState(
            **self.state
        )
        loopstate["node_outputs"][self.node_id] = {
            variable.name: evaluate_expression(
                expression=variable.value,
                conv_var=self.variable_pool.get_all_conversation_vars(),
                node_outputs=self.variable_pool.get_all_node_outputs(),
                system_vars=self.variable_pool.get_all_system_vars(),
            )
            if variable.input_type == ValueInputType.VARIABLE
            else TypeTransformer.transform(variable.value, variable.type)
            for variable in self.typed_config.cycle_vars
        }

        loopstate["looping"] = 1
        loopstate["activate"][self.start_id] = True
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

    def merge_conv_vars(self, loopstate):
        self.variable_pool.variables["conv"].update(
            self.child_variable_pool.variables["conv"]
        )
        loop_vars = self.child_variable_pool.get_node_output(self.node_id, default={}, strict=False)
        loopstate["node_outputs"][self.node_id] = loop_vars

    def evaluate_conditional(self) -> bool:
        """
        Evaluate the loop continuation condition at runtime.

        This method:
        - Resolves all condition expressions against the current workflow state
        - Evaluates each comparison expression immediately
        - Combines results using the configured logical operator (AND / OR)

        Returns:
            bool: True if the loop should continue, False otherwise.
        """
        conditions = []

        for expression in self.typed_config.condition.expressions:
            left_value = self.child_variable_pool.get_value(expression.left)
            evaluator = ConditionExpressionResolver.resolve_by_value(left_value)(
                self.child_variable_pool,
                expression.left,
                expression.right,
                expression.input_type
            )
            conditions.append(self._evaluate(expression.operator, evaluator))
        if self.typed_config.condition.logical_operator == LogicOperator.AND:
            return all(conditions)
        else:
            return any(conditions)

    async def _run(self, loopstate, idx):
        if self.stream:
            async for event in self.graph.astream(
                    loopstate,
                    stream_mode=["debug"],
                    config=self.checkpoint
            ):
                if isinstance(event, tuple) and len(event) == 2:
                    mode, data = event
                else:
                    continue
                if mode == "debug":
                    event_type = data.get("type")
                    payload = data.get("payload", {})
                    node_name = payload.get("name")

                    if node_name and node_name.startswith("nop"):
                        continue
                    if event_type == "task_result":
                        result = payload.get("result", {})
                        node_type = result.get("node_outputs", {}).get(node_name, {}).get("node_type")
                        if not result.get("activate", {}).get(node_name):
                            continue
                        cycle_variable = None
                        if node_type == NodeType.CYCLE_START:
                            cycle_variable = loopstate.get("node_outputs", {}).get(self.node_id, {})
                        self.event_write({
                            "type": "cycle_item",
                            "data": {
                                "cycle_id": self.node_id,
                                "cycle_idx": idx,
                                "node_id": node_name,
                                "input": result.get("node_outputs", {}).get(node_name, {}).get("input")
                                if not cycle_variable else cycle_variable,
                                "output": result.get("node_outputs", {}).get(node_name, {}).get("output")
                                if not cycle_variable else cycle_variable,
                                "elapsed_time": result.get("node_outputs", {}).get(node_name, {}).get("elapsed_time"),
                                "token_usage": result.get("node_outputs", {}).get(node_name, {}).get("token_usage")
                            }
                        })
            return self.graph.get_state(config=self.checkpoint).values
        else:
            return await self.graph.ainvoke(loopstate)

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
        loopstate = await self._init_loop_state()
        loop_time = self.typed_config.max_loop
        child_state = []
        idx = 0
        while not self.evaluate_conditional() and self.looping and loop_time > 0:
            logger.info(f"loop node {self.node_id}: running")
            result = await self._run(loopstate, idx)
            child_state.append(result)

            self.merge_conv_vars(loopstate)
            if result["looping"] == 2:
                self.looping = False
            loop_time -= 1
            idx += 1

        logger.info(f"loop node {self.node_id}: execution completed")
        return self.child_variable_pool.get_node_output(self.node_id, default={}, strict=False) | {"__child_state": child_state}
