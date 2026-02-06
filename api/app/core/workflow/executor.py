"""
工作流执行器

基于 LangGraph 的工作流执行引擎。
"""
import datetime
import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.graph_builder import GraphBuilder, StreamOutputConfig
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.enums import NodeType
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.core.workflow.variable_pool import VariablePool

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Workflow Executor.

    Converts workflow configuration into a LangGraph and executes it,
    supporting both synchronous and streaming execution modes.
    """

    def __init__(
            self,
            workflow_config: dict[str, Any],
            execution_id: str,
            workspace_id: str,
            user_id: str,
    ):
        """Initialize Workflow Executor.

        Converts a workflow configuration into an executor instance that can
        run the workflow in both streaming and non-streaming modes.

        Args:
            workflow_config (dict): The workflow configuration dictionary.
            execution_id (str): Unique identifier for this workflow execution.
            workspace_id (str): Workspace or project ID.
            user_id (str): User ID executing the workflow.

        Attributes:
            self.nodes (list): List of node definitions from workflow_config.
            self.edges (list): List of edge definitions from workflow_config.
            self.execution_config (dict): Optional execution parameters from workflow_config.
            self.start_node_id (str | None): ID of the Start node, set after graph build.
            self.end_outputs (dict[str, StreamOutputConfig]): End node output configs.
            self.activate_end (str | None): Currently active End node ID for streaming outputs.
            self.variable_pool (VariablePool | None): Variable pool instance.
            self.graph (CompiledStateGraph | None): Compiled workflow graph.
            self.checkpoint_config (RunnableConfig): Config for LangGraph checkpointing.
        """
        self.workflow_config = workflow_config
        self.execution_id = execution_id
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.nodes = workflow_config.get("nodes", [])
        self.edges = workflow_config.get("edges", [])
        self.execution_config = workflow_config.get("execution_config", {})

        self.start_node_id = None
        self.end_outputs: dict[str, StreamOutputConfig] = {}
        self.activate_end: str | None = None
        self.variable_pool: VariablePool | None = None

        self.graph: CompiledStateGraph | None = None
        self.checkpoint_config = RunnableConfig(
            configurable={
                "thread_id": uuid.uuid4(),
            }
        )

    async def __init_variable_pool(self, input_data: dict[str, Any]):
        """Initialize the variable pool with system, conversation, and input variables.

        This method populates the VariablePool instance with:
          - Conversation-level variables (`conv` namespace) from workflow config or provided values.
          - System variables (`sys` namespace) such as message, files, conversation_id, execution_id, workspace_id, user_id, and input_variables.

        Args:
            input_data (dict): Input data for workflow execution, may contain:
                - "message": user message (str)
                - "file": list of user-uploaded files
                - "conv": existing conversation variables (dict)
                - "variables": custom variables for the Start node (dict)
                - "conversation_id": conversation identifier
        """
        user_message = input_data.get("message") or ""
        user_files = input_data.get("files") or []

        config_variables_list = self.workflow_config.get("variables") or []
        conv_vars = input_data.get("conv", {})

        # Initialize conversation variables (conv namespace)
        for var_def in config_variables_list:
            var_name = var_def.get("name")
            var_default = conv_vars.get(var_name, var_def.get("default"))
            var_type = var_def.get("type")
            if var_name:
                if var_default:
                    var_value = var_default
                else:
                    var_value = DEFAULT_VALUE(var_type)
                await self.variable_pool.new(
                    namespace="conv",
                    key=var_name,
                    value=var_value,
                    var_type=var_type,
                    mut=True
                )

        # Initialize system variables (sys namespace)
        input_variables = input_data.get("variables") or {}
        sys_vars = {
            "message": (user_message, VariableType.STRING),
            "conversation_id": (input_data.get("conversation_id"), VariableType.STRING),
            "execution_id": (self.execution_id, VariableType.STRING),
            "workspace_id": (self.workspace_id, VariableType.STRING),
            "user_id": (self.user_id, VariableType.STRING),
            "input_variables": (input_variables, VariableType.OBJECT),
            "files": (user_files, VariableType.ARRAY_FILE)
        }
        for key, var_def in sys_vars.items():
            value = var_def[0]
            var_type = var_def[1]
            await self.variable_pool.new(
                namespace='sys',
                key=key,
                value=value,
                var_type=var_type,
                mut=False
            )

    def _prepare_initial_state(self, input_data: dict[str, Any]) -> WorkflowState:
        """Generate the initial workflow state for execution.

        This method prepares the runtime state dictionary with system variables,
        conversation variables, node outputs, loop tracking, and activation flags.

        Args:
            input_data (dict): The input payload for workflow execution.
                Expected keys:
                    - "conv_messages" (list, optional): Historical conversation messages
                      to include in the workflow state.

        Returns:
            WorkflowState: A dictionary representing the initialized workflow state
            with the following keys:
                - "messages": List of conversation messages
                - "node_outputs": Empty dict to store outputs of executed nodes
                - "execution_id": Current workflow execution ID
                - "workspace_id": Current workspace ID
                - "user_id": ID of the user triggering execution
                - "error": None initially, will store error message if a node fails
                - "error_node": None initially, will store ID of node that caused error
                - "cycle_nodes": List of node IDs that are of type LOOP or ITERATION
                - "looping": Integer flag indicating loop execution state (0 = not looping)
                - "activate": Dict mapping node IDs to activation status; initially
                  only the start node is active
        """
        conversation_messages = input_data.get("conv_messages") or []

        return {
            "messages": conversation_messages,
            "node_outputs": {},
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "error": None,
            "error_node": None,
            "cycle_nodes": [
                node.get("id")
                for node in self.workflow_config.get("nodes")
                if node.get("type") in [NodeType.LOOP, NodeType.ITERATION]
            ],  # loop, iteration node id
            "looping": 0,  # loop runing flag, only use in loop node,not use in main loop
            "activate": {
                self.start_node_id: True
            }
        }

    def _build_final_output(self, result, elapsed_time, final_output):
        """Construct the final standardized output of the workflow execution.

        This method aggregates node outputs, token usage, conversation and system
        variables, messages, and other metadata into a consistent dictionary
        structure suitable for returning from workflow execution.

        Args:
            result (dict): The runtime state returned by the workflow graph execution.
                Expected keys include:
                    - "node_outputs" (dict): Outputs of executed nodes.
                    - "messages" (list): Conversation messages exchanged during execution.
                    - "error" (str, optional): Error message if any node failed.
            elapsed_time (float): Total execution time in seconds.
            final_output (Any): The aggregated or final output content of the workflow
                (e.g., combined messages from all End nodes).

        Returns:
            dict: A dictionary containing the final workflow execution result with keys:
                - "status": Execution status ("completed")
                - "output": Aggregated final output content
                - "variables": Namespace dictionary with:
                    - "conv": Conversation variables
                    - "sys": System variables
                - "node_outputs": Outputs from all executed nodes
                - "messages": Conversation messages exchanged
                - "conversation_id": ID of the current conversation
                - "elapsed_time": Total execution time in seconds
                - "token_usage": Aggregated token usage across nodes (if available)
                - "error": Error message if any occurred during execution
        """
        node_outputs = result.get("node_outputs", {})
        token_usage = self._aggregate_token_usage(node_outputs)
        conversation_id = self.variable_pool.get_value("sys.conversation_id")

        return {
            "status": "completed",
            "output": final_output,
            "variables": {
                "conv": self.variable_pool.get_all_conversation_vars(),
                "sys": self.variable_pool.get_all_system_vars()
            },
            "node_outputs": node_outputs,
            "messages": result.get("messages", []),
            "conversation_id": conversation_id,
            "elapsed_time": elapsed_time,
            "token_usage": token_usage,
            "error": result.get("error"),
        }

    def _update_scope_activate(self, scope, status=None):
        """
        Update the activation state of all End nodes based on a completed scope (node or variable).

        Iterates over all End nodes in `self.end_outputs` and calls
        `update_activate` on each, which may:
          - Activate variable segments that depend on the completed node/scope.
          - Activate the entire End node output if any control conditions are met.

        If any End node becomes active and `self.activate_end` is not yet set,
        this node will be marked as the currently active End node.

        Args:
            scope (str): The node ID or scope that has completed execution.
            status (str | None): Optional status of the node (used for branch/control nodes).
        """
        for node in self.end_outputs.keys():
            self.end_outputs[node].update_activate(scope, status)
            if self.end_outputs[node].activate and self.activate_end is None:
                self.activate_end = node

    def _update_stream_output_status(self, activate, data):
        """
        Update the stream output state of End nodes based on workflow state updates.

        This method checks which nodes/scopes are activated and propagates
        activation to End nodes accordingly.

        Args:
            activate (dict): Mapping of node_id -> bool indicating which nodes/scopes are activated.
            data (dict): Mapping of node_id -> node runtime data, including outputs.

        Behavior:
            For each node in `data`:
            1. If the node is activated (`activate[node_id]` is True),
               retrieve its output status from `runtime_vars`.
            2. Call `_update_scope_activate` to propagate the activation
               to all relevant End nodes and update `self.activate_end`.
        """
        for node_id in data.keys():
            if activate.get(node_id):
                node_output_status = self.variable_pool.get_value(f"{node_id}.output", default=None, strict=False)
                self._update_scope_activate(node_id, status=node_output_status)

    async def _emit_active_chunks(
            self,
            force=False
    ):
        """
        Process and yield all currently active output segments for the currently active End node.

        This method handles stream-mode output for an End node by iterating through its output segments
        (`OutputContent`). Only segments marked as active (`activate=True`) are processed, unless
        `force=True`, which allows all segments to be processed regardless of their activation state.

        Behavior:
        1. Iterates from the current `cursor` position to the end of the outputs list.
        2. For each segment:
           - If the segment is literal text (`is_variable=False`), append it directly.
           - If the segment is a variable (`is_variable=True`), evaluate it using
             `evaluate_expression` with the given `node_outputs` and `variables`,
             then transform the result with `_trans_output_string`.
        3. Yield a stream event of type "message" containing the processed chunk.
        4. Move the `cursor` forward after processing each segment.
        5. When all segments have been processed, remove this End node from `end_outputs`
           and reset `activate_end` to None.

        Args:
            force (bool, default=False): If True, process segments even if `activate=False`.

        Yields:
            dict: A stream event of type "message" containing the processed chunk.

        Notes:
            - Segments that fail evaluation (ValueError) are skipped with a warning logged.
            - This method only processes the currently active End node (`self.activate_end`).
            - Use `force=True` for final emission regardless of activation state.
        """

        end_info = self.end_outputs[self.activate_end]

        while end_info.cursor < len(end_info.outputs):
            final_chunk = ''
            current_segment = end_info.outputs[end_info.cursor]

            if not current_segment.activate and not force:
                # Stop processing until this segment becomes active
                break

            # Literal segment
            if not current_segment.is_variable:
                final_chunk += current_segment.literal
            else:
                # Variable segment: evaluate and transform
                try:
                    chunk = self.variable_pool.get_literal(current_segment.literal)
                    final_chunk += chunk
                except KeyError:
                    # Log failed evaluation but continue streaming
                    logger.warning(f"[STREAM] Failed to evaluate segment: {current_segment.literal}")

            if final_chunk:
                yield {
                    "event": "message",
                    "data": {
                        "chunk": final_chunk
                    }
                }

            # Advance cursor after processing
            end_info.cursor += 1

        # Remove End node from active tracking if all segments have been processed
        if end_info.cursor >= len(end_info.outputs):
            self.end_outputs.pop(self.activate_end)
            self.activate_end = None

    async def _handle_updates_event(self, data):
        """
        Handle workflow state update events ("updates") and stream active End node outputs.

        Steps:
        1. Retrieve the current graph state.
        2. Extract node activation information from the state.
        3. Update the activation status of all End nodes.
        4. While there is an active End node:
           - Call _emit_active_chunks() to yield all currently active output segments.
           - After all segments are processed, update activate_end if there are remaining End nodes.
        5. Log a debug message indicating state update received.

        Args:
            data (dict): The latest node state updates.

        Yields:
            dict: Streamed output event, each chunk in the format:
                  {"event": "message", "data": {"chunk": ...}}
        """
        # Get the latest workflow state
        state = self.graph.get_state(config=self.checkpoint_config).values
        activate = state.get("activate", {})

        # Update End node activation based on the new state
        self._update_stream_output_status(activate, data)
        wait = False
        while self.activate_end and not wait:
            async for msg_event in self._emit_active_chunks():
                yield msg_event

            if self.activate_end:
                wait = True
            else:
                self._update_stream_output_status(activate, data)

        logger.debug(f"[UPDATES] Received state update from nodes: {list(data.keys())} "
                     f"- execution_id: {self.execution_id}")

    async def _handle_node_chunk_event(self, data):
        """
        Handle streaming chunk events from individual nodes ("node_chunk").

        This method processes output segments for the currently active End node.
        If the segment depends on the provided node_id:
          - If the node has finished execution (`done=True`), advance the cursor.
          - If all segments are processed, deactivate the End node.
          - Otherwise, yield the current chunk as a streaming message.

        Args:
            data (dict): Node chunk event data, expected keys:
                         - "node_id": ID of the node producing this chunk
                         - "chunk": Chunk of output text
                         - "done": Boolean indicating whether the node finished producing output

        Yields:
            dict: Streaming message event in the format:
                  {"event": "message", "data": {"chunk": ...}}
        """
        node_id = data.get("node_id")
        if self.activate_end:
            end_info = self.end_outputs.get(self.activate_end)
            if not end_info or end_info.cursor >= len(end_info.outputs):
                return
            current_output = end_info.outputs[end_info.cursor]
            if current_output.is_variable and current_output.depends_on_scope(node_id):
                if data.get("done"):
                    end_info.cursor += 1
                    if end_info.cursor >= len(end_info.outputs):
                        self.end_outputs.pop(self.activate_end)
                        self.activate_end = None
                else:
                    yield {
                        "event": "message",
                        "data": {
                            "chunk": data.get("chunk")
                        }
                    }

    async def _handle_node_error_event(self, data):
        """
        Handle node error events ("node_error") during workflow execution.

        This method streams an error event for a node that has failed. The event
        contains the node ID, status, input data, elapsed time, and error message.

        Args:
            data (dict): Node error event data, expected keys:
                         - "node_id": ID of the node that failed
                         - "input_data": The input data that caused the error
                         - "elapsed_time": Execution time before the error occurred
                         - "error": Error message or exception string

        Yields:
            dict: Node error event in the format:
                  {
                      "event": "node_error",
                      "data": {
                          "node_id": str,
                          "status": "failed",
                          "input": ...,
                          "elapsed_time": float,
                          "output": None,
                          "error": str
                      }
                  }
        """
        node_id = data.get("node_id")
        yield {
            "event": "node_error",
            "data": {
                "node_id": node_id,
                "status": "failed",
                "input": data.get("input_data"),
                "elapsed_time": data.get("elapsed_time"),
                "output": None,
                "error": data.get("error")
            }
        }

    async def _handle_debug_event(self, data, input_data):
        """
        Handle debug events ("debug") related to node execution status.

        This method streams debug events for nodes, including when a node starts
        execution ("node_start") and when it completes execution ("node_end").
        It filters out nodes with names starting with "nop" as no-operation nodes.

        Args:
            data (dict): Debug event data, expected keys:
                         - "type": Event type ("task" for start, "task_result" for completion)
                         - "payload": Node-related information, including:
                             - "name": Node name / ID
                             - "input": Node input data (for "task" type)
                             - "result": Node execution result (for "task_result" type)
                         - "timestamp": ISO timestamp string of the event
            input_data (dict): Original workflow input data (used to get conversation_id)

        Yields:
            dict: Node debug event in one of the following formats:
                  1. Node start:
                     {
                         "event": "node_start",
                         "data": {
                             "node_id": str,
                             "conversation_id": str,
                             "execution_id": str,
                             "timestamp": int (ms)
                         }
                     }
                  2. Node end:
                     {
                         "event": "node_end",
                         "data": {
                             "node_id": str,
                             "conversation_id": str,
                             "execution_id": str,
                             "timestamp": int (ms),
                             "input": dict,
                             "output": Any,
                             "elapsed_time": float
                         }
                     }
        """
        event_type = data.get("type")
        payload = data.get("payload", {})
        node_name = payload.get("name")

        # Skip no-operation nodes
        if node_name and node_name.startswith("nop"):
            return

        if event_type == "task":
            # Node starts execution
            inputv = payload.get("input", {})
            if not inputv.get("activate", {}).get(node_name):
                return
            conversation_id = input_data.get("conversation_id")
            logger.info(f"[NODE-START] Node '{node_name}' execution started - execution_id: {self.execution_id}")

            yield {
                "event": "node_start",
                "data": {
                    "node_id": node_name,
                    "conversation_id": conversation_id,
                    "execution_id": self.execution_id,
                    "timestamp": int(datetime.datetime.fromisoformat(
                        data.get("timestamp")
                    ).timestamp() * 1000),
                }
            }
        elif event_type == "task_result":
            # Node execution completed
            result = payload.get("result", {})
            if not result.get("activate", {}).get(node_name):
                return

            conversation_id = input_data.get("conversation_id")
            logger.info(f"[NODE-END] Node '{node_name}' execution completed - execution_id: {self.execution_id}")

            yield {
                "event": "node_end",
                "data": {
                    "node_id": node_name,
                    "conversation_id": conversation_id,
                    "execution_id": self.execution_id,
                    "timestamp": int(datetime.datetime.fromisoformat(
                        data.get("timestamp")
                    ).timestamp() * 1000),
                    "input": result.get("node_outputs", {}).get(node_name, {}).get("input"),
                    "output": result.get("node_outputs", {}).get(node_name, {}).get("output"),
                    "elapsed_time": result.get("node_outputs", {}).get(node_name, {}).get("elapsed_time"),
                    "token_usage": result.get("node_outputs", {}).get(node_name, {}).get("token_usage")
                }
            }

    async def _flush_remaining_chunk(self):
        """
        Flush and yield all remaining output segments from active End nodes.

        This method ensures that any remaining chunks of output, which may not have
        been emitted during normal streaming due to activation conditions, are fully
        processed. It is typically called at the end of a workflow to guarantee
        that all output is delivered.

        Behavior:
        1. Filter `end_outputs` to only keep End nodes that are still active.
        2. While there is an active End node (`self.activate_end`):
           - Call `_emit_active_chunks(force=True)` to emit all segments regardless
             of their activation state.
           - If the current End node finishes, move to the next active End node
             if any remain.

        Yields:
            dict: Streamed output events in the format:
                  {"event": "message", "data": {"chunk": ...}}
        """
        # Keep only active End nodes
        self.end_outputs = {
            node_id: node_info
            for node_id, node_info in self.end_outputs.items()
            if node_info.activate
        }

        if self.end_outputs or self.activate_end:
            while self.activate_end:
                # Force emit all remaining chunks of the active End node
                async for msg_event in self._emit_active_chunks(force=True):
                    yield msg_event

                # Move to next active End node if current one is done
                if not self.activate_end and self.end_outputs:
                    self.activate_end = list(self.end_outputs.keys())[0]

    def build_graph(self, stream=False) -> CompiledStateGraph:
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

        Returns:
            CompiledStateGraph: The compiled and ready-to-run state graph.
        """
        logger.info(f"Starting workflow graph build: execution_id={self.execution_id}")
        builder = GraphBuilder(
            self.workflow_config,
            stream=stream,
        )
        self.start_node_id = builder.start_node_id
        self.end_outputs = builder.end_node_map
        self.variable_pool = builder.variable_pool
        self.graph = builder.build()
        logger.info(f"Workflow graph build completed: execution_id={self.execution_id}")

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
        logger.info(f"Starting workflow execution: execution_id={self.execution_id}")

        start_time = datetime.datetime.now()

        # Build the workflow graph
        graph = self.build_graph()

        # Initialize the variable pool with input data
        await self.__init_variable_pool(input_data)
        initial_state = self._prepare_initial_state(input_data)

        # Execute the workflow
        try:
            result = await graph.ainvoke(initial_state, config=self.checkpoint_config)

            # Aggregate output from all End nodes
            full_content = ''
            for end_id in self.end_outputs.keys():
                full_content += self.variable_pool.get_value(f"{end_id}.output", default="", strict=False)

            # Append messages for user and assistant
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
            # Calculate elapsed time
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.info(f"Workflow execution completed: execution_id={self.execution_id}, elapsed_time={elapsed_time:.2f}s")

            return self._build_final_output(result, elapsed_time, full_content)

        except Exception as e:
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.error(f"Workflow execution failed: execution_id={self.execution_id}, error={e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "output": None,
                "node_outputs": {},
                "elapsed_time": elapsed_time,
                "token_usage": None
            }

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
        logger.info(f"Starting workflow execution (streaming): execution_id={self.execution_id}")

        start_time = datetime.datetime.now()

        yield {
            "event": "workflow_start",
            "data": {
                "execution_id": self.execution_id,
                "workspace_id": self.workspace_id,
                "conversation_id": input_data.get("conversation_id"),
                "timestamp": int(start_time.timestamp() * 1000)
            }
        }

        # Build the workflow graph in streaming mode
        graph = self.build_graph(stream=True)

        # Initialize the variable pool and system variables
        await self.__init_variable_pool(input_data)
        initial_state = self._prepare_initial_state(input_data)


        try:
            full_content = ''
            self._update_scope_activate("sys")

            # Execute the workflow with streaming
            async for event in graph.astream(
                    initial_state,
                    stream_mode=["updates", "debug", "custom"],  # Use updates + debug + custom mode
                    config=self.checkpoint_config
            ):
                # event should be a tuple: (mode, data)
                # But let's handle both cases
                if isinstance(event, tuple) and len(event) == 2:
                    mode, data = event
                else:
                    # Unexpected format, log and skip
                    logger.warning(f"[STREAM] Unexpected event format: {type(event)}, value: {event}"
                                   f"- execution_id: {self.execution_id}")
                    continue

                if mode == "custom":
                    # Handle custom streaming events (chunks from nodes via stream writer)
                    event_type = data.get("type", "node_chunk")  # "message" or "node_chunk"
                    if event_type == "node_chunk":
                        async for msg_event in self._handle_node_chunk_event(data):
                            full_content += data.get("chunk")
                            yield msg_event

                    elif event_type == "node_error":
                        async for error_event in self._handle_node_error_event(data):
                            yield error_event

                elif mode == "debug":
                    async for debug_event in self._handle_debug_event(data, input_data):
                        yield debug_event

                elif mode == "updates":
                    logger.debug(f"[UPDATES] 收到 state 更新 from {list(data.keys())} "
                                 f"- execution_id: {self.execution_id}")
                    async for msg_event in self._handle_updates_event(data):
                        full_content += msg_event["data"]['chunk']
                        yield msg_event

            # Flush any remaining chunks
            async for msg_event in self._flush_remaining_chunk():
                full_content += msg_event["data"]['chunk']
                yield msg_event

            result = graph.get_state(self.checkpoint_config).values
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            # Append messages for user and assistant
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
                f"elapsed: {elapsed_time:.2f}s, execution_id: {self.execution_id}"
            )

            yield {
                "event": "workflow_end",
                "data": self._build_final_output(result, elapsed_time, full_content)
            }

        except Exception as e:
            end_time = datetime.datetime.now()
            elapsed_time = (end_time - start_time).total_seconds()

            logger.error(f"Workflow execution failed: execution_id={self.execution_id}, error={e}", exc_info=True)

            yield {
                "event": "workflow_end",
                "data": {
                    "execution_id": self.execution_id,
                    "status": "failed",
                    "error": str(e),
                    "elapsed_time": elapsed_time,
                    "timestamp": end_time.isoformat()
                }
            }

    @staticmethod
    def _aggregate_token_usage(node_outputs: dict[str, Any]) -> dict[str, int] | None:
        """
        Aggregate token usage statistics across all nodes.

        Args:
            node_outputs (dict): A dictionary of all node outputs.

        Returns:
            dict | None: Aggregated token usage in the format:
                         {
                             "prompt_tokens": int,
                             "completion_tokens": int,
                             "total_tokens": int
                         }
                         Returns None if no token usage information is available.
        """
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        has_token_info = False

        for node_output in node_outputs.values():
            if isinstance(node_output, dict):
                token_usage = node_output.get("token_usage")
                if token_usage and isinstance(token_usage, dict):
                    has_token_info = True
                    total_prompt_tokens += token_usage.get("prompt_tokens", 0)
                    total_completion_tokens += token_usage.get("completion_tokens", 0)
                    total_tokens += token_usage.get("total_tokens", 0)

        if not has_token_info:
            return None

        return {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens
        }


async def execute_workflow(
        workflow_config: dict[str, Any],
        input_data: dict[str, Any],
        execution_id: str,
        workspace_id: str,
        user_id: str
) -> dict[str, Any]:
    """
    Execute a workflow (convenience function, non-streaming).

    Args:
        workflow_config (dict): The workflow configuration.
        input_data (dict): Input data for the workflow.
        execution_id (str): Execution ID.
        workspace_id (str): Workspace ID.
        user_id (str): User ID.

    Returns:
        dict: Workflow execution result.
    """
    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id
    )
    return await executor.execute(input_data)


async def execute_workflow_stream(
        workflow_config: dict[str, Any],
        input_data: dict[str, Any],
        execution_id: str,
        workspace_id: str,
        user_id: str
):
    """
    Execute a workflow in streaming mode (convenience function).

    Args:
        workflow_config (dict): The workflow configuration.
        input_data (dict): Input data for the workflow.
        execution_id (str): Execution ID.
        workspace_id (str): Workspace ID.
        user_id (str): User ID.

    Yields:
        dict: Streaming workflow events, e.g. node start, node end, chunk messages, workflow end.
    """
    executor = WorkflowExecutor(
        workflow_config=workflow_config,
        execution_id=execution_id,
        workspace_id=workspace_id,
        user_id=user_id
    )
    async for event in executor.execute_stream(input_data):
        yield event
