"""Helper functions for serializing tool parameters."""

from typing import Any, Dict

from ..schemas.tool_schema import ToolParameter


def serialize_tool_parameter(param: ToolParameter) -> Dict[str, Any]:
    """Support both enum and string values of ToolParameter.type."""
    param_type = getattr(param.type, "value", param.type)
    return {
        "name": param.name,
        "type": param_type,
        "description": param.description,
        "required": param.required,
        "default": param.default,
        "enum": param.enum,
        "minimum": param.minimum,
        "maximum": param.maximum,
        "pattern": param.pattern,
    }
