# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/9 13:51
import time
import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.engine.event_stream_handler import EventStreamHandler
from app.core.workflow.engine.graph_builder import GraphBuilder
from app.core.workflow.engine.result_builder import WorkflowResultBuilder
from app.core.workflow.engine.runtime_schema import ExecutionContext
from app.core.workflow.engine.state_manager import WorkflowStateManager
from app.core.workflow.engine.stream_output_coordinator import StreamOutputCoordinator
from app.core.workflow.engine.variable_pool import VariablePool, VariablePoolInitializer
from app.core.workflow.nodes.base_node import NodeExecutionError
from app.core.utils.datetime_utils import to_timestamp_ms, utcnow_naive

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Workflow Executor.

    Converts workflow configuration into a LangGraph and executes it,
    supporting both synchronous and streaming execution modes.
    """

    def __init__(
            self,
            workflow_config: dict[str, Any],
            execution_context: ExecutionContext,
    ):
        """Initialize Workflow Executor.

        Converts a workflow configuration into an executor instance that can
        run the workflow in both streaming and non-streaming modes.

        Args:
            workflow_config (dict): The workflow configuration dictionary.
            execution_context (ExecutionContext): The workflow execution context
            include execution_id, workspace_id, user_id, checkpoint_config

        Attributes:
            self.execution_config (dict): Optional execution parameters from workflow_config.
            self.start_node_id (str | None): ID of the Start node, set after graph build.
            self.end_outputs (dict[str, StreamOutputConfig]): End node output configs.
            self.activate_end (str | None): Currently active End node ID for streaming outputs.
            self.variable_pool (VariablePool | None): Variable pool instance.
            self.graph (CompiledStateGraph | None): Compiled workflow graph.
            self.checkpoint_config (RunnableConfig): Config for LangGraph checkpointing.
        """
        self.workflow_config = workflow_config
        self.execution_context = execution_context
        self.execution_config = workflow_config.get("execution_config", {})

        self.start_node_id: str | None = None
        self.variable_pool: VariablePool | None = None
        self.graph: CompiledStateGraph | None = None

        self.variable_initializer = VariablePoolInitializer(workflow_config)
        self.state_manager = WorkflowStateManager()
        self.result_builder = WorkflowResultBuilder()
        self.stream_coordinator = StreamOutputCoordinator()
        self.event_handler: EventStreamHandler | None = None

    def build_graph(self, stream=False, checkpointer=None) -> CompiledStateGraph:
        """
        Build the workflow graph using LangGraph.

        This method initializes a GraphBuilder with the workflow configuration,
        builds the compiled state graph, and sets up the executor's key attributes:
          - `start_node_id`: the ID of the start node in the workflow
          - `end_outputs`: mapping of End nodes and their output configurations
          - `variable_pool`: pool containing workflow variables
          - `graph`: the compiled state graph ready for execution

        Args:
            stream (bool, optional): Whether to enable streaming mode. Defaults to False.
            checkpointer: Optional cached checkpointer for resuming interrupted workflows.

        Returns:
            CompiledStateGraph: The compiled and ready-to-run state graph.
        """
        logger.info(f"Starting workflow graph build: execution_id={self.execution_context.execution_id}")
        start_time = time.time()
        builder = GraphBuilder(
            self.workflow_config,
            stream=stream,
        )

        if checkpointer is None:
            thread_id = str(
                self.execution_context.checkpoint_config
                .get("configurable", {})
                .get("thread_id", "")
            )
            if thread_id:
                from app.core.workflow.engine.graph_builder import get_or_create_checkpointer
                checkpointer = get_or_create_checkpointer(thread_id)

        self.graph = builder.build(checkpointer=checkpointer)
        self.start_node_id = builder.start_node_id
        self.variable_pool = builder.variable_pool

        self.stream_coordinator.initialize_end_outputs(builder.end_node_map)
        self.event_handler = EventStreamHandler(
            output_coordinator=self.stream_coordinator,
            variable_pool=self.variable_pool,
            execution_id=self.execution_context.execution_id
        )
        logger.info(f"Workflow graph build completed: execution_id={self.execution_context.execution_id}, "
                    f"cost: {time.time() - start_time:.4f}s")

        return self.graph

    async def execute(
            self,
            input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Execute the workflow in non-streaming (batch) mode.

        Steps:
        1. Build the workflow graph.
        2. Initialize the variable pool and inject system variables.
        3. Prepare the initial workflow state.
        4. Invoke the compiled graph and collect outputs.
        5. Aggregate outputs, messages, and token usage.

        Args:
            input_data (dict): Input data including 'message' and 'variables'.

        Returns:
            dict: Execution result containing:
                  - status: "completed" or "failed"
                  - output: aggregated output string from all End nodes
                  - variables: current conversation and system variables
                  - node_outputs: all node outputs
                  - messages: list of messages including user and assistant content
                  - elapsed_time: workflow execution time in seconds
                  - token_usage: aggregated token usage if available
                  - error: error message if any
        """
        start = utcnow_naive()
        async for event in self.execute_stream(input_data):
            if event.get("event") == "workflow_end":
                return event.get("data")
        return self.result_builder.build_final_output(
            {"error": "Workflow execution did not end as expected"},
            self.execution_context,
            self.variable_pool,
            (utcnow_naive() - start).total_seconds(),
            "",
            success=False
        )

    async def execute_stream(
            self,
            input_data: dict[str, Any]
    ):
        """
        Execute the workflow in streaming mode.

        Supports multiple streaming modes:
        1. "updates" - Node state updates and streaming chunks.
        2. "debug" - Detailed node execution info (start/end).
        3. "custom" - Custom streaming chunks from nodes.

        Args:
            input_data (dict): Input data including 'message', 'variables', etc.

        Yields:
            dict: Streaming events in the format:
                  {
                      "event": "workflow_start" | "workflow_end" | "node_start" |
                               "node_end" | "node_chunk" | "message",
                      "data": {...}
                  }
        """
        logger.info(f"Starting workflow execution (streaming): execution_id={self.execution_context.execution_id}")

        start_time = utcnow_naive()

        yield {
            "event": "workflow_start",
            "data": {
                "execution_id": self.execution_context.execution_id,
                "workspace_id": self.execution_context.workspace_id,
                "conversation_id": self.execution_context.conversation_id,
                "timestamp": to_timestamp_ms(start_time)
            }
        }
        result = None
        full_content = ''
        try:
            # Build the workflow graph in streaming mode
            graph = self.build_graph(stream=True)

            # Initialize the variable pool and system variables
            await self.variable_initializer.initialize(
                variable_pool=self.variable_pool,
                input_data=input_data,
                execution_context=self.execution_context
            )
            initial_state = self.state_manager.create_initial_state(
                workflow_config=self.workflow_config,
                input_data=input_data,
                execution_context=self.execution_context,
                start_node_id=self.start_node_id
            )

            self.stream_coordinator.update_scope_activation("sys")

            # Execute the workflow with streaming
            async for event in graph.astream(
                    initial_state,
                    stream_mode=["updates", "debug", "custom"],  # Use updates + debug + custom mode
                    config=self.execution_context.checkpoint_config
            ):
                # event should be a tuple: (mode, data)
                # But let's handle both cases
                if isinstance(event, tuple) and len(event) == 2:
                    mode, data = event
                else:
                    # Unexpected format, log and skip
                    logger.warning(f"[STREAM] Unexpected event format: {type(event)}, value: {event}"
                                   f"- execution_id: {self.execution_context.execution_id}")
                    continue

                if mode == "custom":
                    # Handle custom streaming events (chunks from nodes via stream writer)
                    event_type = data.get("type", "node_chunk")  # "message" or "node_chunk"
                    try:
                        if event_type == "node_chunk":
                            async for msg_event in self.event_handler.handle_node_chunk_event(data):
                                full_content += msg_event["data"]["content"]
                                yield msg_event

                        elif event_type == "node_error":
                            async for error_event in self.event_handler.handle_node_error_event(data):
                                yield error_event

                        elif event_type == "cycle_item":
                            async for cycle_event in self.event_handler.handle_cycle_item_event(data):
                                yield cycle_event
                        elif event_type == "agent_log":
                            async for agent_log_event in self.event_handler.handle_agent_log_event(data):
                                yield agent_log_event

                        elif event_type in ("agent_tool_start", "agent_tool_end", "agent_tool_error"):
                        # Agent 节点工具调用中间状态，直接透传给前端展示
                            yield {
                                "event": event_type,
                                "data": {
                                    "node_id": data.get("node_id"),
                                    "step_id": data.get("step_id"),
                                    "tool_name": data.get("tool_name"),
                                    "tool_input": data.get("tool_input"),
                                    "tool_output": data.get("tool_output"),
                                    "error": data.get("error"),
                                    "meta": data.get("meta"),
                                }
                            }

                    except Exception as custom_err:
                        logger.error(f"[STREAM] Error handling custom event ({event_type}): {custom_err} "
                                     f"- execution_id={self.execution_context.execution_id}")

                elif mode == "debug":
                    try:
                        async for debug_event in self.event_handler.handle_debug_event(data, input_data):
                            yield debug_event
                    except Exception as debug_err:
                        logger.error(f"[STREAM] Error handling debug event: {debug_err} "
                                     f"- execution_id={self.execution_context.execution_id}")

                elif mode == "updates":
                    logger.debug(f"[UPDATES] 收到 state 更新 from {list(data.keys())} "
                                 f"- execution_id: {self.execution_context.execution_id}")
                    try:
                        async for msg_event in self.event_handler.handle_updates_event(
                                data,
                                self.graph,
                                self.execution_context.checkpoint_config
                        ):
                            full_content += msg_event["data"]['content']
                            yield msg_event
                    except Exception as updates_err:
                        logger.error(f"[STREAM] Error handling updates event: {updates_err} "
                                     f"- execution_id={self.execution_context.execution_id}")


            graph_state = await graph.aget_state(self.execution_context.checkpoint_config)

            # Use the top-level flat interrupts tuple (more reliable than traversing tasks)
            all_interrupts = tuple(getattr(graph_state, 'interrupts', ()) or ())

            is_interrupted = bool(all_interrupts)

            if is_interrupted:
                end_time = utcnow_naive()
                elapsed_time = (end_time - start_time).total_seconds()
                logger.info(
                    f"Workflow interrupted for human intervention: "
                    f"execution_id={self.execution_context.execution_id}, "
                    f"langgraph_interrupts={len(all_interrupts)}, "
                    f"tasks={len(getattr(graph_state, 'tasks', ()))}"
                )

                # Collect interrupts from LangGraph state
                interventions = []
                collected_node_ids = set()
                for intr in all_interrupts:
                    intr_value = getattr(intr, 'value', None)
                    if intr_value and isinstance(intr_value, dict):
                        intr_copy = dict(intr_value)
                        intr_copy["__interrupt_id__"] = intr.id
                        interventions.append(intr_copy)
                        collected_node_ids.add(intr_value.get("node_id", ""))

                # Merge with InterventionRegistry to recover lost interrupts.
                # LangGraph 1.0.10 _panic_or_proceed may cancel inflight parallel tasks
                # when one raises GraphInterrupt, losing their interrupt data.
                from app.core.workflow.nodes.human_intervention.node import InterventionRegistry
                all_registered = InterventionRegistry.get_all(self.execution_context.execution_id)
                for node_id, reg_data in all_registered.items():
                    if node_id not in collected_node_ids:
                        logger.warning(
                            f"Recovering lost interrupt from registry: node={node_id}, "
                            f"execution_id={self.execution_context.execution_id}"
                        )
                        synthetic = dict(reg_data)
                        synthetic["__interrupt_id__"] = ""
                        synthetic["__from_registry__"] = True
                        interventions.append(synthetic)

                # NOTE: Do NOT clean up InterventionRegistry here.
                # The registry data must persist across resume cycles so that
                # subsequent resume_workflow_stream() calls can recover remaining
                # lost interrupts (e.g., node_C when only node_A/B are in LangGraph).
                # Cleanup happens in resume_workflow_stream() when the workflow
                # fully completes (no remaining interventions).

                logger.info(
                    f"Final interventions count: {len(interventions)}, "
                    f"from_langgraph={len(collected_node_ids)}, "
                    f"from_registry={len(interventions) - len(collected_node_ids)}"
                )
                
                yield {
                    "event": "workflow_end",
                    "data": {
                        "status": "waiting_human",
                        "execution_id": self.execution_context.execution_id,
                        "elapsed_time": elapsed_time,
                        "node_outputs": graph_state.values.get("node_outputs", {}) if graph_state.values else {},
                        "checkpoint_thread_id": str(
                            self.execution_context.checkpoint_config.get("configurable", {}).get("thread_id", "")
                        ),
                        "interventions": interventions,
                    }
                }
                return

            # Clean up registry in the non-interrupt path (no interventions were registered,
            # or they were already cleaned up above)
            from app.core.workflow.nodes.human_intervention.node import InterventionRegistry
            InterventionRegistry.cleanup(self.execution_context.execution_id)

            # Clean up checkpointer from cache to prevent memory leak
            thread_id = str(self.execution_context.checkpoint_config.get("configurable", {}).get("thread_id", ""))
            if thread_id:
                from app.core.workflow.engine.graph_builder import remove_checkpointer
                remove_checkpointer(thread_id)

            # Flush any remaining chunks
            async for msg_event in self.stream_coordinator.flush_remaining_chunk(self.variable_pool):
                full_content += msg_event["data"]['content']
                yield msg_event

            result = (await graph.aget_state(self.execution_context.checkpoint_config)).values
            end_time = utcnow_naive()
            elapsed_time = (end_time - start_time).total_seconds()

            # For output nodes, collect structured results from variable_pool and serialize to JSON
            output_node_ids = [
                node["id"] for node in self.workflow_config.get("nodes", [])
                if node.get("type") == "output"
            ]
            if output_node_ids:
                structured_output = {}
                for node_id in output_node_ids:
                    node_output = self.variable_pool.get_node_output(node_id, default=None, strict=False)
                    if node_output:
                        structured_output.update(node_output)
                final_output = structured_output if structured_output else full_content
            else:
                final_output = full_content

            # Append messages for user and assistant
            if input_data.get("files"):
                result["messages"].extend(
                    [
                        {
                            "role": "user",
                            "content": input_data.get("message", '')
                        },
                        {
                            "role": "user",
                            "content": input_data.get("files")
                        },
                        {
                            "role": "assistant",
                            "content": full_content
                        }
                    ]
                )
            else:
                result["messages"].extend(
                    [
                        {
                            "role": "user",
                            "content": input_data.get("message", '')
                        },
                        {
                            "role": "assistant",
                            "content": full_content
                        }
                    ]
                )
            logger.info(
                f"Workflow execution completed (streaming), "
                f"elapsed: {elapsed_time:.2f}ms, execution_id: {self.execution_context.execution_id}"
            )

            yield {
                "event": "workflow_end",
                "data": self.result_builder.build_final_output(
                    result,
                    self.execution_context,
                    self.variable_pool,
                    elapsed_time,
                    final_output,
                    success=True)
            }

        except Exception as e:
            end_time = utcnow_naive()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.error(f"Workflow execution failed: execution_id={self.execution_context.execution_id}, error={e}",
                         exc_info=True)

            # 1) 尝试从 checkpoint 回补已成功节点的 node_outputs
            recovered: dict[str, Any] = {}
            try:
                if self.graph is not None:
                    recovered = self.graph.get_state(
                        self.execution_context.checkpoint_config
                    ).values or {}
            except Exception as recover_err:
                logger.warning(
                    f"Recover state on failure failed: {recover_err}, "
                    f"execution_id={self.execution_context.execution_id}"
                )

            if result is None:
                result = dict(recovered) if recovered else {}
            else:
                # 已有 result 与 recovered 合并，node_outputs 深度合并
                for k, v in recovered.items():
                    if k == "node_outputs" and isinstance(v, dict):
                        existing = result.get("node_outputs") or {}
                        result["node_outputs"] = {**v, **existing}
                    else:
                        result.setdefault(k, v)

            # 2) 如果是节点抛出的 NodeExecutionError，把失败节点的 node_output 注入 node_outputs
            failed_node_id: str | None = None
            if isinstance(e, NodeExecutionError):
                failed_node_id = e.node_id
                node_outputs = result.setdefault("node_outputs", {})
                # 不覆盖已有（理论上不会有），保底写入失败节点记录
                node_outputs.setdefault(e.node_id, e.node_output)

            result["error"] = str(e)
            if failed_node_id:
                result["error_node"] = failed_node_id

            yield {
                "event": "workflow_end",
                "data": self.result_builder.build_final_output(
                    result,
                    self.execution_context,
                    self.variable_pool,
                    elapsed_time,
                    full_content,
                    success=False
                )
            }


async def execute_workflow(
        workflow_config: dict[str, Any],
        input_data: dict[str, Any],
        execution_id: str,
        workspace_id: str,
        user_id: str,
        memory_storage_type: str,
        user_rag_memory_id: str
) -> dict[str, Any]:
    """
    Execute a workflow (convenience function, non-streaming).

    Args:
        workflow_config (dict): The workflow configuration.
        input_data (dict): Input data for the workflow.
        execution_id (str): Execution ID.
        workspace_id (str): Workspace ID.
        user_id (str): User ID.
        user_rag_memory_id: rag knowledge db id
        memory_storage_type: neo4j / rag

    Returns:
        dict: Workflow execution result.
    """
    execution_context = ExecutionContext.create(
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id,
        conversation_id=input_data.get("conversation_id"),
        memory_storage_type=memory_storage_type,
        user_rag_memory_id=user_rag_memory_id
    )
    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        execution_context=execution_context
    )
    return await executor.execute(input_data)


async def execute_workflow_stream(
        workflow_config: dict[str, Any],
        input_data: dict[str, Any],
        execution_id: str,
        workspace_id: str,
        user_id: str,
        memory_storage_type: str,
        user_rag_memory_id: str
):
    """
    Execute a workflow in streaming mode (convenience function).

    Args:
        workflow_config (dict): The workflow configuration.
        input_data (dict): Input data for the workflow.
        execution_id (str): Execution ID.
        workspace_id (str): Workspace ID.
        user_id (str): User ID.
        user_rag_memory_id: rag knowledge db id
        memory_storage_type: neo4j / rag

    Yields:
        dict: Streaming workflow events, e.g. node start, node end, chunk messages, workflow end.
    """
    execution_context = ExecutionContext.create(
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id,
        memory_storage_type=memory_storage_type,
        conversation_id=input_data.get("conversation_id"),
        user_rag_memory_id=user_rag_memory_id
    )
    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        execution_context=execution_context
    )
    async for event in executor.execute_stream(input_data):
        yield event
