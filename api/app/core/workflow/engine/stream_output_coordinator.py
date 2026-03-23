# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/9 15:11
import re
from queue import Queue
from typing import AsyncGenerator

from pydantic import BaseModel, Field, PrivateAttr

from app.core.logging_config import get_logger
from app.core.workflow.engine.variable_pool import VariablePool

logger = get_logger(__name__)

SCOPE_PATTERN = re.compile(
    r"\{\{\s*([a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\s*}}"
)


class OutputContent(BaseModel):
    """
    Represents a single output segment of an End node.

    An output segment can be either:
    - literal text (static string)
    - a variable placeholder (e.g. {{ node.field }})

    Each segment has its own activation state, which is especially
    important in stream mode.
    """

    literal: str = Field(
        ...,
        description="Raw output content. Can be literal text or a variable placeholder."
    )

    activate: bool = Field(
        ...,
        description=(
            "Whether this output segment is currently active."
            "- True: allowed to be emitted/output"
            "- False: blocked until activated by branch control"
        )
    )

    is_variable: bool = Field(
        ...,
        description=(
            "Whether this segment represents a variable placeholder."
            "True  -> variable (e.g. {{ node.field }})"
            "False -> literal text"
        )
    )

    _SCOPE: str | None = PrivateAttr(default=None)

    def get_scope(self) -> str | None:
        matches = SCOPE_PATTERN.findall(self.literal)
        self._SCOPE = matches[0] if matches else None
        return self._SCOPE

    def depends_on_scope(self, scope: str) -> bool:
        """
        Check if this segment depends on a given scope.

        Args:
            scope (str): Node ID or special variable prefix (e.g., "sys").

        Returns:
            bool: True if this segment references the given scope.
        """
        if not self.is_variable:
            return False
        if self._SCOPE:
            return self._SCOPE == scope
        return self.get_scope() == scope


class StreamOutputConfig(BaseModel):
    """
    Streaming output configuration for an End node.

    This configuration describes how the End node output behaves in streaming mode,
    including:
    - whether output emission is globally activated
    - which upstream branch/control nodes gate the activation
    - how each parsed output segment is streamed and activated
    """
    id: str = Field(
        ...,
        description="ID of the End node this configuration belongs to."
    )

    activate: bool = Field(
        ...,
        description=(
            "Global activation flag for the End node output."
            "When False, output segments should not be emitted even if available."
            "This flag typically becomes True once required control branch conditions "
            "are satisfied."
        )
    )

    control_nodes: dict[str, list[str]] = Field(
        ...,
        description=(
            "Control branch conditions for this End node output."
            "Mapping of `branch_node_id -> expected_branch_label`."
            "The End node output becomes globally active when a controlling branch node "
            "reports a matching completion status."
        )
    )

    upstream_output_nodes: list[str] = Field(
        ...,
        description=(
            "Upstream output node dependencies (data flow)."
            "Represents END/output nodes that this output depends on."
            "These nodes provide data sources required before this output can be activated "
            "or streamed."
            "Used to ensure correct ordering and dependency resolution in streaming mode."
        )
    )

    control_resolved: bool = Field(
        ...,
        description=(
            "Whether all upstream branch control dependencies have been satisfied."
            "True if no upstream branch nodes exist or the required branch "
            "conditions have been met."
        )
    )

    output_resolved: bool = Field(
        ...,
        description=(
            "Whether all upstream output node dependencies have been completed."
            "True if no upstream output nodes exist or all upstream output "
            "nodes have finished their output."
        )
    )

    outputs: list[OutputContent] = Field(
        ...,
        description=(
            "Ordered list of output segments parsed from the output template."
            "Each segment represents either a literal text block or a variable placeholder "
            "that may be activated independently."
        )
    )

    cursor: int = Field(
        ...,
        description=(
            "Streaming cursor index."
            "Indicates the next output segment index to be emitted."
            "Segments with index < cursor are considered already streamed."
        )
    )

    force: bool = Field(
        default=False,
        description=(
            "Force flag for output emission."
            "When True, all output segments are emitted regardless of activation state."
            "Triggered when this output node has finished execution."
        )
    )

    def update_activate(self, scope: str, status=None):
        """
        Update streaming activation state based on upstream events.

        Args:
            scope (str):
                Identifier of the completed upstream entity.
                - If a control branch node, it should match a key in `control_nodes`.
                - If an upstream output node, it should match an entry in `upstream_output_nodes`.
                - If a variable placeholder (e.g., "sys.xxx" or "node_id.field"),
                  it may appear in output segments.

            status (optional):
                Completion status of the control branch node.
                Required when `scope` refers to a control node.

        Behavior:
        1. Force activation:
           - If `self.force` is True, the method returns immediately.
           - If `scope == self.id`, the node marks itself as completed:
               - `activate = True`
               - `force = True`
             This is typically used for final flushing when the node finishes execution.

        2. Control dependency resolution:
           - If `scope` matches a key in `control_nodes`:
               - `status` must be provided.
               - If `status` matches expected branch labels, mark control as resolved
                 (`control_resolved = True`).

        3. Upstream output dependency resolution:
           - If `scope` is in `upstream_output_nodes`,
             mark data dependency as resolved (`output_resolved = True`).

        4. Global activation condition:
           - The node becomes active when BOTH conditions are satisfied:
               - control_resolved == True
               - output_resolved == True
           - Once activated, `activate` remains True.

        5. Variable segment activation:
           - For each output segment that is a variable (`is_variable=True`):
               - If the segment depends on the given `scope`,
                 mark the segment as active.
           - This applies to both node variables (e.g., "node_id.field")
             and system variables (e.g., "sys.xxx").

        Notes:
        - This method does NOT emit output or advance the streaming cursor.
        - It only updates activation and dependency resolution states.
        - Activation is driven by both control flow (branch nodes) and
          data flow (upstream output nodes).
        """
        if self.force:
            return

        if scope == self.id:
            self.activate = True
            self.force = True
            return

        # resolve control branch dependency
        if scope in self.control_nodes:
            if status is None:
                raise RuntimeError("[Stream Output] Control node activation status not provided")
            if status in self.control_nodes[scope]:
                self.control_resolved = True

        if scope in self.upstream_output_nodes:
            self.upstream_output_nodes.remove(scope)
        if not self.upstream_output_nodes:
            self.output_resolved = True

        self.activate = self.activate or (self.control_resolved and self.output_resolved)

        # activate variable segments related to this node
        for i in range(len(self.outputs)):
            if (
                    self.outputs[i].is_variable
                    and self.outputs[i].depends_on_scope(scope)
            ):
                self.outputs[i].activate = True


class StreamOutputCoordinator:
    def __init__(self):
        self.end_outputs: dict[str, StreamOutputConfig] = {}
        self.activate_end: str | None = None
        self.output_queue: Queue = Queue()
        self.processed_outputs = []

    def initialize_end_outputs(
            self,
            end_node_map: dict[str, StreamOutputConfig]
    ):
        self.end_outputs = end_node_map
        self.processed_outputs = []
        self.activate_end = None
        self.output_queue = Queue()

    @property
    def current_activate_end_info(self):
        return self.end_outputs.get(self.activate_end)

    def pop_current_activate_end(self):
        self.end_outputs.pop(self.activate_end)
        self.activate_end = None

    def update_scope_activation(
            self,
            scope: str,
            status: str | None = None
    ):
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
            if self.end_outputs[node].activate and node not in self.processed_outputs:
                self.output_queue.put(node)
                self.processed_outputs.append(node)
        if self.activate_end is None and not self.output_queue.empty():
            self.activate_end = self.output_queue.get_nowait()

    async def emit_activate_chunk(
            self,
            variable_pool: VariablePool,
            force: bool = False
    ) -> AsyncGenerator[dict[str, str | dict], None]:
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
            variable_pool (VariablePool): Pool of variables for evaluating segment values.
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

            if not current_segment.activate and not force and not end_info.force:
                # Stop processing until this segment becomes active
                break

            # Literal segment
            if not current_segment.is_variable:
                final_chunk += current_segment.literal
            else:
                # Variable segment: evaluate and transform
                try:
                    chunk = variable_pool.get_literal(current_segment.literal)
                    final_chunk += chunk
                except Exception as e:
                    # Log failed evaluation but continue streaming
                    logger.warning(f"[STREAM] Failed to evaluate segment: {current_segment.literal}, error: {e}")

            if final_chunk:
                logger.info(f"[STREAM] StreamOutput Node:{self.activate_end}, chunk_length:{len(final_chunk)}")
                yield {
                    "event": "message",
                    "data": {
                        "content": final_chunk
                    }
                }

            # Advance cursor after processing
            end_info.cursor += 1

        if end_info.cursor >= len(end_info.outputs):
            self.pop_current_activate_end()

    async def flush_remaining_chunk(
            self,
            variable_pool: VariablePool
    ) -> AsyncGenerator[dict[str, str | dict], None]:
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
                async for msg_event in self.emit_activate_chunk(variable_pool, force=True):
                    yield msg_event

                if not self.output_queue.empty():
                    self.activate_end = self.output_queue.get_nowait()
                # Move to next active End node if current one is done
                if not self.activate_end and self.end_outputs:
                    self.activate_end = list(self.end_outputs.keys())[0]
