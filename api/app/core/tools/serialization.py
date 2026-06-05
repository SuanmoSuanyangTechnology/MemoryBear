"""工具参数序列化辅助函数。"""

from typing import Any, Dict

from app.schemas.tool_schema import ToolParameter


def serialize_tool_parameter(param: ToolParameter) -> Dict[str, Any]:
    """兼容 ToolParameter.type 为枚举或字符串两种情况。"""
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
