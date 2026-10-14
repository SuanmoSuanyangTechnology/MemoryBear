"""Datetime tool - date and time handling."""
from typing import List

from ...schemas.tool_schema import ToolParameter, ParameterType
from .base import BuiltinTool


class DateTimeTool(BuiltinTool):
    """Datetime tool - format, timezone, timestamp conversion and time calculation."""

    @property
    def name(self) -> str:
        return "datetime_tool"

    @property
    def description(self) -> str:
        return "时间工具 - 日期时间处理：提供时间格式转化、时区转换、时间戳转换、时间计算"

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="操作类型",
                required=True,
                enum=["format", "convert_timezone", "timestamp_to_datetime", "now", "datetime_to_timestamp"]
            ),
            ToolParameter(
                name="input_value",
                type=ParameterType.STRING,
                description="输入值（时间字符串或时间戳）",
                required=False
            ),
            ToolParameter(
                name="input_format",
                type=ParameterType.STRING,
                description="输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                required=False,
                default="%Y-%m-%d %H:%M:%S"
            ),
            ToolParameter(
                name="output_format",
                type=ParameterType.STRING,
                description="输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                required=False,
                default="%Y-%m-%d %H:%M:%S"
            ),
            ToolParameter(
                name="from_timezone",
                type=ParameterType.STRING,
                description="源时区（如：UTC, Asia/Shanghai）",
                required=False,
                default="Asia/Shanghai"
            ),
            ToolParameter(
                name="to_timezone",
                type=ParameterType.STRING,
                description="目标时区（如：UTC, Asia/Shanghai）",
                required=False,
                default="Asia/Shanghai"
            ),
            ToolParameter(
                name="calculation",
                type=ParameterType.STRING,
                description="时间计算表达式（如：+1d, -2h, +30m）",
                required=False
            )
        ]
