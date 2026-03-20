# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/9 15:11
import re
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
            "Whether this output segment is currently active.\n"
            "- True: allowed to be emitted/output\n"
            "- False: blocked until activated by branch control"
        )
    )

    is_variable: bool = Field(
        ...,
        description=(
            "Whether this segment represents a variable placeholder.\n"
            "True  -> variable (e.g. {{ node.field }})\n"
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

    activate: bool = Field(
        ...,
        description=(
            "Global activation flag for the End node output.\n"
            "When False, output segments should not be emitted even if available.\n"
            "This flag typically becomes True once required control branch conditions "
            "are satisfied."
        )
    )

    control_nodes: dict[str, list[str]] = Field(
        ...,
        description=(
            "Control branch conditions for this End node output.\n"
            "Mapping of `branch_node_id -> expected_branch_label`.\n"
            "The End node output becomes globally active when a controlling branch node "
            "reports a matching completion status."
        )
    )

    outputs: list[OutputContent] = Field(
        ...,
        description=(
            "Ordered list of output segments parsed from the output template.\n"
            "Each segment represents either a literal text block or a variable placeholder "
            "that may be activated independently."
        )
    )

    cursor: int = Field(
        ...,
        description=(
            "Streaming cursor index.\n"
            "Indicates the next output segment index to be emitted.\n"
            "Segments with index < cursor are considered already streamed."
        )
    )

    def update_activate(self, scope: str, status=None):
        """
        Update streaming activation state based on an upstream node or special variable.

        Args:
            scope (str):
                Identifier of the completed upstream entity.
                - If a control branch node, it should match a key in `control_nodes`.
                - If a variable placeholder (e.g., "sys.xxx"), it may appear in output segments.
            status (optional):
                Completion status of the control branch node.
                Required when `scope` refers to a control node.

        Behavior:
        1. Control branch nodes:
           - If `scope` matches a key in `control_nodes` and `status` matches the expected
             branch label, the End node output becomes globally active (`activate = True`).

        2. Variable output segments:
           - For each segment that is a variable (`is_variable=True`):
               - If the segment literal references `scope`, mark the segment as active.
               - This applies both to regular node variables (e.g., "node_id.field")
                 and special system variables (e.g., "sys.xxx").

        Notes:
        - This method does not emit output or advance the streaming cursor.
        - It only updates activation flags based on upstream events or special variables.
        """

        # Case 1: resolve control branch dependency
        if scope in self.control_nodes:
            if status is None:
                raise RuntimeError("[Stream Output] Control node activation status not provided")
            if status in self.control_nodes[scope]:
                self.activate = True

        # Case 2: activate variable segments related to this node
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

    def initialize_end_outputs(
            self,
            end_node_map: dict[str, StreamOutputConfig]
    ):
        self.end_outputs = end_node_map

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
            if self.end_outputs[node].activate and self.activate_end is None:
                self.activate_end = node

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

            if not current_segment.activate and not force:
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
                logger.info(f"[STREAM] StreamOutput Node:{self.activate_end}, chunk:{final_chunk}")
                yield {
                    "event": "message",
                    "data": {
                        "content": final_chunk
                    }
                }

            # Advance cursor after processing
            end_info.cursor += 1

        if end_info.cursor >= len(end_info.outputs):
            self.end_outputs.pop(self.activate_end)
            self.activate_end = None

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

                # Move to next active End node if current one is done
                if not self.activate_end and self.end_outputs:
                    self.activate_end = list(self.end_outputs.keys())[0]
