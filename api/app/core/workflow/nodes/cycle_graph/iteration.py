import asyncio
import logging
import re
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.config import get_stream_writer

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.cycle_graph import IterationNodeConfig
from app.core.workflow.nodes.enums import NodeType, IterationErrorHandleMode
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE

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
            stream: bool,
            node_id: str,
            config: dict[str, Any],
            state: WorkflowState,
            variable_pool: VariablePool,
            cycle_nodes: list,
            cycle_edges: list,
    ):
        """
        Initialize the iteration runtime.

        Args:
            stream:       Whether to run in streaming mode. When True, each iteration
                          uses graph.astream and emits cycle_item events in real time.
                          When False, graph.ainvoke is used instead.
            node_id:      The unique identifier of the iteration node in the workflow.
                          Also used as the variable namespace for item/index inside
                          the subgraph (e.g. {{ node_id.item }}).
            config:       Raw configuration dict for the iteration node, parsed into
                          IterationNodeConfig. Controls input/output variable selectors,
                          parallel execution settings, and output flattening.
            state:        The parent workflow state at the point the iteration node is
                          entered. Each task receives a copy of this state as its
                          starting point.
            variable_pool: The parent VariablePool containing all variables available
                           at the time the iteration node executes, including sys.*,
                           conv.*, and outputs from upstream nodes. Used as the source
                           for deep-copying into each task's independent child pool.
            cycle_nodes:  List of node config dicts belonging to this iteration's
                          subgraph (i.e. nodes whose cycle field equals node_id).
                          Passed to GraphBuilder when constructing each task's subgraph.
            cycle_edges:  List of edge config dicts connecting nodes within the subgraph.
                          Passed to GraphBuilder alongside cycle_nodes.
        """
        self.stream = stream
        self.state = state
        self.node_id = node_id
        self.typed_config = IterationNodeConfig(**config)
        self.looping = True
        self.variable_pool = variable_pool
        self.cycle_nodes = cycle_nodes
        self.cycle_edges = cycle_edges
        self.event_write = get_stream_writer() if self.stream else (lambda x: None)

        self.output_value = None
        self.result: list = []

    def _build_child_graph(self) -> tuple[CompiledStateGraph, VariablePool, str]:
        """
        Build an independent compiled subgraph for a single iteration task.

        Each call creates a brand-new VariablePool by deep-copying the parent pool,
        then passes it to GraphBuilder. GraphBuilder binds this pool to every node's
        execution closure at build time, so the pool and the subgraph always reference
        the same object. This is the key design invariant: item/index written into the
        pool after build will be visible to all nodes inside the subgraph.

        Returns:
            graph:      The compiled LangGraph subgraph ready for invocation.
            child_pool: The VariablePool bound to this subgraph's node closures.
                        Callers must write item/index into this pool before invoking
                        the graph, and read output from it after invocation.
            start_node_id: The ID of the CYCLE_START node inside the subgraph,
                           used to set the initial activation signal in workflow state.
        """
        from app.core.workflow.engine.graph_builder import GraphBuilder
        child_pool = VariablePool()
        child_pool.copy(self.variable_pool)
        builder = GraphBuilder(
            {"nodes": self.cycle_nodes, "edges": self.cycle_edges},
            stream=self.stream,
            variable_pool=child_pool,
            cycle=self.node_id,
        )
        graph = builder.build()
        return graph, builder.variable_pool, builder.start_node_id

    async def _init_iteration_state(self, item, idx, child_pool: VariablePool, start_id: str):
        """
        Initialize the workflow state for a single iteration.

        Writes the current item and its index into child_pool under the iteration
        node's namespace (e.g. iteration_xxx.item, iteration_xxx.index), making them
        accessible to downstream nodes inside the subgraph via variable selectors.

        Also prepares a copy of the parent workflow state with:
        - node_outputs[node_id] set to {item, index} so the state snapshot is consistent
          with the pool values.
        - looping flag set to 1 (active) to signal the subgraph is inside a cycle.
        - activate[start_id] set to True to trigger the CYCLE_START node.

        Args:
            item:       The current element from the input array.
            idx:        The zero-based index of this element in the input array.
            child_pool: The VariablePool bound to this iteration's subgraph.
                        Must be the same object returned by _build_child_graph.
            start_id:   The ID of the CYCLE_START node inside the subgraph.

        Returns:
            A WorkflowState instance ready to be passed to graph.ainvoke or graph.astream.
        """
        loopstate = WorkflowState(**self.state)
        await child_pool.new(self.node_id, "item", item, VariableType.type_map(item), mut=True)
        await child_pool.new(self.node_id, "index", idx, VariableType.type_map(idx), mut=True)
        loopstate["node_outputs"][self.node_id] = {"item": item, "index": idx}
        loopstate["looping"] = 1
        loopstate["activate"][start_id] = True
        return loopstate

    def _merge_conv_vars(self, child_pool: VariablePool):
        self.variable_pool.variables["conv"].update(child_pool.variables["conv"])

    async def run_task(self, item, idx):
        """
        Execute a single iteration asynchronously.
        Each task builds its own subgraph so the variable pool closure is independent.

        Returns:
            Tuple of (idx, output, result, child_pool, stopped, success)
            success is False when the iteration encountered an error.
        """
        graph, child_pool, start_id = self._build_child_graph()
        checkpoint = RunnableConfig(configurable={"thread_id": uuid.uuid4()})
        init_state = await self._init_iteration_state(item, idx, child_pool, start_id)

        try:
            if self.stream:
                async for event in graph.astream(
                        init_state,
                        stream_mode=["debug"],
                        config=checkpoint
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
                            if not result.get("activate", {}).get(node_name):
                                continue
                            node_type = result.get("node_outputs", {}).get(node_name, {}).get("node_type")
                            cycle_variable = {"item": item} if node_type == NodeType.CYCLE_START else None
                            node_cfg = next(
                                (n for n in self.cycle_nodes if n.get("id") == node_name), None
                            )
                            self.event_write({
                                "type": "cycle_item",
                                "data": {
                                    "cycle_id": self.node_id,
                                    "cycle_idx": idx,
                                    "node_id": node_name,
                                    "node_type": node_type,
                                    "node_name": node_cfg.get("data", {}).get("label") if node_cfg else node_name,
                                    "status": result.get("node_outputs", {}).get(node_name, {}).get("status", "completed"),
                                    "input": result.get("node_outputs", {}).get(node_name, {}).get("input")
                                    if not cycle_variable else cycle_variable,
                                    "output": result.get("node_outputs", {}).get(node_name, {}).get("output")
                                    if not cycle_variable else cycle_variable,
                                    "elapsed_time": result.get("node_outputs", {}).get(node_name, {}).get("elapsed_time"),
                                    "token_usage": result.get("node_outputs", {}).get(node_name, {}).get("token_usage")
                                }
                            })
                result = graph.get_state(config=checkpoint).values
            else:
                result = await graph.ainvoke(init_state, config=checkpoint)

            output = child_pool.get_value(self.output_value)
            stopped = result["looping"] == 2
            return idx, output, result, child_pool, stopped, True
        except Exception as e:
            logger.warning(f"Iteration node {self.node_id}: iteration idx={idx} failed: {e}")
            # Emit a failed cycle_item event for visibility
            if self.stream:
                self.event_write({
                    "type": "cycle_item",
                    "data": {
                        "cycle_id": self.node_id,
                        "cycle_idx": idx,
                        "node_id": self.node_id,
                        "node_type": "iteration",
                        "node_name": self.node_id,
                        "status": "failed",
                        "input": {"item": item, "index": idx},
                        "output": None,
                        "elapsed_time": 0,
                        "token_usage": None,
                        "error_message": str(e),
                    }
                })
            return idx, None, None, None, False, False

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
        error_mode = self.typed_config.error_handle_mode
        idx = 0
        if self.typed_config.parallel:
            # Execute iterations in parallel batches
            while idx < len(array_obj) and self.looping:
                tasks = self._create_iteration_tasks(array_obj, idx)
                logger.info(f"Iteration node {self.node_id}: running, concurrency {len(tasks)}")
                idx += self.typed_config.parallel_count
                batch = await asyncio.gather(*tasks)
                # Sort by idx to preserve order, then collect results
                batch_sorted = sorted(batch, key=lambda x: x[0])
                for _, output, result, child_pool, stopped, success in batch_sorted:
                    if not success:
                        if error_mode == IterationErrorHandleMode.TERMINATED:
                            raise RuntimeError(f"Iteration node {self.node_id}: iteration failed, terminating")
                        elif error_mode == IterationErrorHandleMode.CONTINUE_ON_ERROR:
                            self.result.append(DEFAULT_VALUE(self.typed_config.output_type or VariableType.NUMBER))
                        # REMOVE_ABNORMAL_OUTPUT: skip appending
                        continue
                    if isinstance(output, list) and self.typed_config.flatten:
                        self.result.extend(output)
                    else:
                        self.result.append(output)
                    if child_pool:
                        child_state.append(result)
                        self._merge_conv_vars(child_pool)
                    if stopped:
                        self.looping = False
        else:
            # Execute iterations sequentially
            while idx < len(array_obj) and self.looping:
                logger.info(f"Iteration node {self.node_id}: running")
                item = array_obj[idx]
                _, output, result, child_pool, stopped, success = await self.run_task(item, idx)
                if not success:
                    if error_mode == IterationErrorHandleMode.TERMINATED:
                        raise RuntimeError(f"Iteration node {self.node_id}: iteration failed, terminating")
                    elif error_mode == IterationErrorHandleMode.CONTINUE_ON_ERROR:
                        self.result.append(DEFAULT_VALUE(self.typed_config.output_type or VariableType.NUMBER))
                    # REMOVE_ABNORMAL_OUTPUT: skip appending
                    idx += 1
                    continue
                if isinstance(output, list) and self.typed_config.flatten:
                    self.result.extend(output)
                else:
                    self.result.append(output)
                self._merge_conv_vars(child_pool)
                child_state.append(result)
                if stopped:
                    self.looping = False
                idx += 1
        logger.info(f"Iteration node {self.node_id}: execution completed")
        return {
            "output": self.result,
            "__child_state": child_state
        }
