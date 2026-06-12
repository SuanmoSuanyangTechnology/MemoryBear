# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/10 13:33
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.core.logging_config import get_logger
from app.core.utils.datetime_utils import parse_iso_to_utc_naive, to_timestamp_ms
from app.core.workflow.engine.stream_output_coordinator import StreamOutputCoordinator
from app.core.workflow.engine.variable_pool import VariablePool

logger = get_logger(__name__)


class EventStreamHandler:
    def __init__(
            self,
            output_coordinator: StreamOutputCoordinator,
            variable_pool: VariablePool,
            execution_id: str,
    ):
        self.coordinator = output_coordinator
        self.variable_pool = variable_pool
        self.execution_id = execution_id

    def _mask(self, value):
        return value

    def update_stream_output_status(self, activate: dict, data: dict):
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
                node_data = data.get(node_id)
                if not isinstance(node_data, dict):
                    logger.debug(
                        f"[UPDATES] skip non-dict update for node {node_id}: "
                        f"type={type(node_data).__name__}, value={node_data!r}"
                    )
                    continue
                node_output_status = self.variable_pool.get_value(f"{node_id}.output", default=None, strict=False)
                if node_output_status is None:
                    node_outputs = node_data.get("node_outputs", {}) or {}
                    node_output_info = node_outputs.get(node_id, {}) or {}
                    raw_output = node_output_info.get("output")
                    if isinstance(raw_output, dict):
                        node_output_status = raw_output.get("output")
                    elif raw_output is not None:
                        node_output_status = raw_output
                # NOP control nodes return {"activate": {target: bool}} instead
                # of having an "output" field. Extract the activation signal.
                if node_output_status is None:
                    node_data_raw = data[node_id]
                    act_dict = node_data_raw.get("activate")
                    if isinstance(act_dict, dict) and act_dict:
                        node_output_status = str(list(act_dict.values())[0])
                # Branch nodes that use internal routing fields (e.g. human_intervention
                # uses __route, LLM uses branch_signal) strip these from the visible
                # output via _extract_output, so they won't appear in node_outputs.
                # The variable pool still has them (injected by _inject_route_variable),
                # so look there as a fallback.
                if node_output_status is None:
                    for route_field in ("__route", "branch_signal"):
                        route_value = self.variable_pool.get_value(
                            f"{node_id}.{route_field}", default=None, strict=False
                        )
                        if route_value is not None:
                            node_output_status = route_value
                            break
                self.coordinator.update_scope_activation(node_id, status=node_output_status)

    async def handle_updates_event(
            self,
            data: dict,
            graph: CompiledStateGraph,
            checkpoint_config: RunnableConfig
    ):
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
            graph (CompiledStateGraph): The compiled LangGraph state machine.
            checkpoint_config (RunnableConfig): Configuration for the current execution context.)

        Yields:
            dict: Streamed output event, each chunk in the format:
                  {"event": "message", "data": {"chunk": ...}}
        """
        state = await graph.aget_state(config=checkpoint_config)
        activate = state.values.get("activate", {}) if state.values else {}

        self.update_stream_output_status(activate, data)
        wait = False
        while self.coordinator.activate_end and not wait:
            async for msg_event in self.coordinator.emit_activate_chunk(self.variable_pool):
                yield msg_event

            if self.coordinator.activate_end:
                wait = True
            else:
                self.update_stream_output_status(activate, data)

        logger.debug(f"[UPDATES] Received state update from nodes: {list(data.keys())} "
                     f"- execution_id: {self.execution_id}")

    async def handle_node_chunk_event(self, data: dict):
        """
        Handle streaming chunk events from individual nodes ("node_chunk").

        This method processes output segments for the currently active End node.
        It handles literal prefixes before variable segments, emits chunks directly
        when there's no active End node (fallback), and tracks streamed scopes
        to prevent duplicate emission in emit_activate_chunk.

        Literal-text segments between variable segments are emitted automatically
        so the cursor can advance past them during streaming without waiting for
        emit_activate_chunk.

        Args:
            data (dict): Node chunk event data, expected keys:
                         - "node_id": ID of the node producing this chunk
                         - "chunk": Chunk of output text
                         - "done": Boolean indicating whether the node finished producing output
                         - "field": Field name of the chunk (e.g. "output" or "reasoning_content")

        Yields:
            dict: Streaming message event in the format:
                  {"event": "message", "data": {"content": ...}}
        """
        node_id = data.get("node_id")
        chunk = data.get("chunk")
        done = data.get("done")

        if self.coordinator.activate_end:
            end_info = self.coordinator.current_activate_end_info
            if not end_info or end_info.cursor >= len(end_info.outputs):
                return

            # Scan from cursor to find the variable segment that depends on node_id.
            # If there are literal segments before it, emit them first (literal prefix handling).
            # This handles templates like "Result: {{llm.output}}"
            target_segment_idx = None
            for i in range(end_info.cursor, len(end_info.outputs)):
                seg = end_info.outputs[i]
                if seg.is_variable and seg.depends_on_scope(node_id):
                    target_segment_idx = i
                    break

            if target_segment_idx is not None and target_segment_idx > end_info.cursor:
                # Emit literal/variable segments between cursor and target
                for i in range(end_info.cursor, target_segment_idx):
                    seg = end_info.outputs[i]
                    if not seg.is_variable:
                        yield {"event": "message", "data": {"content": self._mask(seg.literal)}}
                    else:
                        # Another variable segment before our target - resolve from pool
                        try:
                            val = self.variable_pool.get_literal(seg.literal)
                            yield {"event": "message", "data": {"content": self._mask(val)}}
                        except Exception:
                            pass
                # Advance cursor to the target variable segment
                end_info.cursor = target_segment_idx

            current_output = end_info.outputs[end_info.cursor]
            if current_output.is_variable and current_output.depends_on_scope(node_id):
                # Field-level matching: route real-time chunks only to the segment
                # that references the same field. Chunks carrying "reasoning_content"
                # flow to {{node.reasoning_content}}, "output" to {{node.output}}.
                chunk_field = data.get("field", "output")
                segment_field = current_output.get_field() or "output"
                if chunk_field != segment_field:
                    return

                if done:
                    # Mark scope as streamed to prevent duplicate emission in emit_activate_chunk
                    self.coordinator.mark_scope_streamed(node_id, chunk_field)
                    end_info.cursor += 1
                    if end_info.cursor >= len(end_info.outputs):
                        self.coordinator.pop_current_activate_end()
                else:
                    yield {
                        "event": "message",
                        "data": {
                            "content": self._mask(chunk)
                        }
                    }
        else:
            # Fallback: No active End node, but chunks are arriving.
            # Only emit directly for End nodes that are already activated (no branch control).
            # End nodes still waiting for branch routing must NOT receive chunks here.
            dependent_ends = self.coordinator.find_ends_dependent_on_scope(node_id)
            active_dependent_ends = [(eid, einfo) for eid, einfo in dependent_ends if einfo.activate]
            if active_dependent_ends:
                chunk_field = data.get("field", "output")
                has_matching_segment = any(
                    seg.is_variable
                    and seg.depends_on_scope(node_id)
                    and (seg.get_field() or "output") == chunk_field
                    for _, end_info in active_dependent_ends
                    for seg in end_info.outputs
                )
                if not has_matching_segment:
                    return
                if done:
                    self.coordinator.mark_scope_streamed(node_id, chunk_field)
                elif chunk:
                    yield {
                        "event": "message",
                        "data": {
                            "content": self._mask(chunk)
                        }
                    }

    async def handle_node_error_event(self, data: dict):
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
        payload = {
            "event": "node_error",
            "data": {
                "node_id": node_id,
                "status": "failed",
                "input": data.get("input_data"),
                "output": None,
                "process": data.get("process_data"),
                "elapsed_time": data.get("elapsed_time"),
                "error": data.get("error")
            }
        }
        yield self._mask(payload)

    async def handle_debug_event(self, data: dict, input_data: dict):
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
        conversation_id = input_data.get("conversation_id")

        # Skip no-operation nodes
        if node_name and node_name.startswith("nop"):
            return

        if event_type == "task":
            # Node starts execution
            inputv = payload.get("input", {})
            if not inputv.get("activate", {}).get(node_name):
                return

            logger.info(
                f"[NODE-START] Node '{node_name}' execution started - execution_id: {self.execution_id}")

            yield {
                "event": "node_start",
                "data": {
                    "node_id": node_name,
                    "conversation_id": conversation_id,
                    "execution_id": self.execution_id,
                    "timestamp": to_timestamp_ms(parse_iso_to_utc_naive(data.get("timestamp"))),
                }
            }
        elif event_type == "task_result":
            # Node execution completed
            result = payload.get("result", {})
            if not result.get("activate", {}).get(node_name):
                return

            logger.info(
                f"[NODE-END] Node '{node_name}' execution completed - execution_id: {self.execution_id}")

            payload = {
                "event": "node_end",
                "data": {
                    "node_id": node_name,
                    "conversation_id": conversation_id,
                    "execution_id": self.execution_id,
                    "timestamp": to_timestamp_ms(parse_iso_to_utc_naive(data.get("timestamp"))),
                    "input": result.get("node_outputs", {}).get(node_name, {}).get("input"),
                    "output": result.get("node_outputs", {}).get(node_name, {}).get("output"),
                    "process": result.get("node_outputs", {}).get(node_name, {}).get("process"),
                    "agent_log": result.get("node_outputs", {}).get(node_name, {}).get("agent_log"),
                    "elapsed_time": result.get("node_outputs", {}).get(node_name, {}).get("elapsed_time"),
                    "token_usage": result.get("node_outputs", {}).get(node_name, {}).get("token_usage")
                }
            }
            yield self._mask(payload)

    async def handle_cycle_item_event(self, data: dict):
        yield self._mask({
            "event": "cycle_item",
            "data": data.get("data")
        })

    async def handle_agent_log_event(self, data: dict):
        yield self._mask({
            "event": "agent_log",
            "data": data.get("data")
        })
