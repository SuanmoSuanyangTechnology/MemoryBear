import asyncio
import logging
import re
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.cycle_graph import IterationNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class IterationRuntime:
    """
    Runtime executor for loop/iteration nodes in a workflow.

    This class handles executing iterations over a list variable, supporting
    optional parallel execution, flattening of output, and loop control via
    the workflow state.
    """

    def __init__(
            self,
            start_id: str,
            graph: CompiledStateGraph,
            node_id: str,
            config: dict[str, Any],
            state: WorkflowState,
            variable_pool: VariablePool,
            child_variable_pool: VariablePool,
    ):
        """
        Initialize the iteration runtime.

        Args:
            graph: Compiled workflow graph capable of async invocation.
            node_id: Unique identifier of the loop node.
            config: Dictionary containing iteration node configuration.
            state: Current workflow state at the point of iteration.
        """
        self.start_id = start_id
        self.graph = graph
        self.state = state
        self.node_id = node_id
        self.typed_config = IterationNodeConfig(**config)
        self.looping = True
        self.variable_pool = variable_pool
        self.child_variable_pool = child_variable_pool

        self.output_value = None
        self.result: list = []

    async def _init_iteration_state(self, item, idx):
        """
        Initialize a per-iteration copy of the workflow state.

        Args:
            item: Current element from the input array for this iteration.
            idx: Index of the element in the input array.

        Returns:
            A copy of the workflow state with iteration-specific variables set.
        """
        loopstate = WorkflowState(
            **self.state
        )
        self.child_variable_pool.copy(self.variable_pool)
        await self.child_variable_pool.new(self.node_id, "item", item, VariableType.type_map(item), mut=True)
        await self.child_variable_pool.new(self.node_id, "index", item, VariableType.type_map(item), mut=True)
        loopstate["node_outputs"][self.node_id] = {
            "item": item,
            "index": idx,
        }
        loopstate["looping"] = 1
        loopstate["activate"][self.start_id] = True
        return loopstate

    def merge_conv_vars(self):
        self.variable_pool.get_all_conversation_vars().update(
            self.child_variable_pool.get_all_conversation_vars()
        )

    async def run_task(self, item, idx):
        """
        Execute a single iteration asynchronously.

        Args:
            item: The input element for this iteration.
            idx: The index of this iteration.
        """
        result = await self.graph.ainvoke(await self._init_iteration_state(item, idx))
        output = self.child_variable_pool.get_value(self.output_value)
        if isinstance(output, list) and self.typed_config.flatten:
            self.result.extend(output)
        else:
            self.result.append(output)
        if result["looping"] == 2:
            self.looping = False
        return result

    def _create_iteration_tasks(self, array_obj, idx):
        """
        Create async tasks for a batch of iterations based on parallel count.

        Args:
            array_obj: The input array to iterate over.
            idx: Starting index for this batch of iterations.

        Returns:
            List of coroutine tasks ready to be executed in parallel.
        """
        tasks = []
        for i in range(self.typed_config.parallel_count):
            if idx + i >= len(array_obj):
                break
            item = array_obj[idx + i]
            tasks.append(self.run_task(item, idx + i))
        return tasks

    async def run(self):
        """
        Execute the loop over the input array according to configuration.

        Returns:
            A list of outputs from all iterations, optionally flattened.

        Raises:
            RuntimeError: If the input variable is not a list.
        """
        pattern = r"\{\{\s*(.*?)\s*\}\}"
        input_expression = re.sub(pattern, r"\1", self.typed_config.input).strip()
        self.output_value = re.sub(pattern, r"\1", self.typed_config.output).strip()

        array_obj = self.variable_pool.get_value(input_expression)
        if not isinstance(array_obj, list):
            raise RuntimeError("Cannot iterate over a non-list variable")
        child_state = []
        idx = 0
        if self.typed_config.parallel:
            # Execute iterations in parallel batches
            while idx < len(array_obj) and self.looping:
                tasks = self._create_iteration_tasks(array_obj, idx)
                logger.info(f"Iteration node {self.node_id}: running, concurrency {len(tasks)}")
                idx += self.typed_config.parallel_count
                child_state.extend(await asyncio.gather(*tasks))
                self.merge_conv_vars()
        else:
            # Execute iterations sequentially
            while idx < len(array_obj) and self.looping:
                logger.info(f"Iteration node {self.node_id}: running")
                item = array_obj[idx]
                result = await self.graph.ainvoke(await self._init_iteration_state(item, idx))
                child_state.append(result)
                output = self.child_variable_pool.get_value(self.output_value)
                self.merge_conv_vars()
                if isinstance(output, list) and self.typed_config.flatten:
                    self.result.extend(output)
                else:
                    self.result.append(output)
                if result["looping"] == 2:
                    self.looping = False
                idx += 1
        logger.info(f"Iteration node {self.node_id}: execution completed")
        return {
            "output": self.result,
            "__child_state": child_state
        }
