"""JSON conversion tool - data format conversion."""
from typing import List

from ..base import ToolParameter, ParameterType
from .base import BuiltinTool


class JsonTool(BuiltinTool):
    """JSON conversion tool - formatting, minification, validation and format conversion."""

    @property
    def name(self) -> str:
        return "json_tool"

    @property
    def description(self) -> str:
        return "JSON转换工具 - 数据格式转换：JSON格式化、JSON压缩、JSON验证、格式转换"

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="操作类型",
                required=True,
                enum=["insert", "replace", "delete", "parse"]
            ),
            ToolParameter(
                name="input_data",
                type=ParameterType.STRING,
                description="输入数据（JSON字符串、YAML字符串或XML字符串）",
                required=True
            ),
            ToolParameter(
                name="indent",
                type=ParameterType.INTEGER,
                description="JSON格式化缩进空格数",
                required=False,
                default=2,
                minimum=0,
                maximum=8
            ),
            ToolParameter(
                name="ensure_ascii",
                type=ParameterType.BOOLEAN,
                description="是否确保ASCII编码",
                required=False,
                default=False
            ),
            ToolParameter(
                name="sort_keys",
                type=ParameterType.BOOLEAN,
                description="是否对键进行排序",
                required=False,
                default=False
            ),
            ToolParameter(
                name="merge_data",
                type=ParameterType.STRING,
                description="要合并的JSON数据（用于merge操作）",
                required=False
            ),
            ToolParameter(
                name="json_path",
                type=ParameterType.STRING,
                description="JSON路径表达式（用于insert、replace、delete、parse操作，如：$.user.name或users[0].name）",
                required=False
            ),
            ToolParameter(
                name="new_value",
                type=ParameterType.STRING,
                description="新值（用于insert和replace操作）",
                required=False
            ),
            ToolParameter(
                name="old_text",
                type=ParameterType.STRING,
                description="要替换的原文本（用于replace操作）",
                required=False
            ),
            ToolParameter(
                name="new_text",
                type=ParameterType.STRING,
                description="替换后的新文本（用于replace操作）",
                required=False
            )
        ]
