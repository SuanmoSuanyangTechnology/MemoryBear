"""Shared normalization for second/third-level tool selections.

List display, save validation and runtime expansion must agree on which
entries are whole-tool selections: workflow tools never expose operations,
and builtin tools without an ``operation`` parameter cannot be scoped.
"""

from __future__ import annotations

from ..models.tool_model import ToolType


def normalize_tool_selection(
    tool_type: str | None,
    operation: str | None,
    *,
    has_operation_enum: bool = True,
) -> str | None:
    """Return the operation that should be persisted for a tool selection."""

    if not operation:
        return None
    if tool_type == ToolType.WORKFLOW.value:
        return None
    if tool_type == ToolType.BUILTIN.value and not has_operation_enum:
        return None
    return operation


__all__ = ["normalize_tool_selection"]
