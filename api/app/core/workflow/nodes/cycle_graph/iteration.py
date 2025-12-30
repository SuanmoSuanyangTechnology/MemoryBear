import asyncio
import copy
import logging
import re
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.cycle_graph import IterationNodeConfig
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
            graph: CompiledStateGraph,
            node_id: str,
            config: dict[str, Any],
            state: WorkflowState,
    ):
        """
        Initialize the iteration runtime.

        Args:
            graph: Compiled workflow graph capable of async invocation.
            node_id: Unique identifier of the loop node.
            config: Dictionary containing iteration node configuration.
            state: Current workflow state at the point of iteration.
        """
        self.graph = graph
        self.state = state
        self.node_id = node_id
        self.typed_config = IterationNodeConfig(**config)
        self.looping = True

        self.output_value = None
        self.result: list = []

    def _init_iteration_state(self, item, idx):
        """
        Initialize a per-iteration copy of the workflow state.

        Args:
            item: Current element from the input array for this iteration.
            idx: Index of the element in the input array.

        Returns:
            A deep copy of the workflow state with iteration-specific variables set.
        """
        loopstate = WorkflowState(
            **copy.deepcopy(self.state)
        )
        loopstate["runtime_vars"][self.node_id] = {
            "item": item,
            "index": idx,
        }
        loopstate["node_outputs"][self.node_id] = {
            "item": item,
            "index": idx,
        }
        loopstate["looping"] = True
        return loopstate

    async def run_task(self, item, idx):
        """
        Execute a single iteration asynchronously.

        Args:
            item: The input element for this iteration.
            idx: The index of this iteration.
        """
        result = await self.graph.ainvoke(self._init_iteration_state(item, idx))
        output = VariablePool(result).get(self.output_value)
        if isinstance(output, list) and self.typed_config.flatten:
            self.result.extend(output)
        else:
            self.result.append(output)
        if not result["looping"]:
            self.looping = False

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

        array_obj = VariablePool(self.state).get(input_expression)
        if not isinstance(array_obj, list):
            raise RuntimeError("Cannot iterate over a non-list variable")

        idx = 0
        if self.typed_config.parallel:
            # Execute iterations in parallel batches
            while idx < len(array_obj) and self.looping:
                tasks = self._create_iteration_tasks(array_obj, idx)
                logger.info(f"Iteration node {self.node_id}: running, concurrency {len(tasks)}")
                idx += self.typed_config.parallel_count
                await asyncio.gather(*tasks)
            logger.info(f"Iteration node {self.node_id}: execution completed")
            return self.result
        else:
            # Execute iterations sequentially
            while idx < len(array_obj) and self.looping:
                logger.info(f"Iteration node {self.node_id}: running")
                item = array_obj[idx]
                result = await self.graph.ainvoke(self._init_iteration_state(item, idx))
                output = VariablePool(result).get(self.output_value)
                if isinstance(output, list) and self.typed_config.flatten:
                    self.result.extend(output)
                else:
                    self.result.append(output)
                if not result["looping"]:
                    self.looping = False
                idx += 1

            logger.info(f"Iteration node {self.node_id}: execution completed")
            return self.result
