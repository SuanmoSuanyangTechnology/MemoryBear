"""Operation tool - wraps a base tool bound to one specific operation."""
from typing import List

from ...models.tool_model import ToolType
from ...schemas.tool_schema import ParameterType, ToolParameter, ToolResult
from ..base import BaseTool


class OperationTool(BaseTool):
    """Wraps a base tool so it exposes only one operation's parameters."""

    def __init__(self, base_tool: BaseTool, operation: str):
        self.base_tool = base_tool
        self.operation = operation
        super().__init__(base_tool.tool_id, base_tool.config)

    def set_runtime_context(self, **kwargs):
        """Forward the runtime context to the wrapped tool."""
        if hasattr(self.base_tool, 'set_runtime_context'):
            self.base_tool.set_runtime_context(**kwargs)

    @property
    def name(self) -> str:
        return f"{self.base_tool.name}_{self.operation}"

    @property
    def tool_type(self) -> ToolType:
        return ToolType.BUILTIN

    @property
    def description(self) -> str:
        return f"{self.base_tool.description} - {self.operation}"

    @property
    def parameters(self) -> List[ToolParameter]:
        """Return the parameter definitions for the bound operation."""
        if self.base_tool.name == 'datetime_tool':
            return self._get_datetime_params()
        elif self.base_tool.name == 'json_tool':
            return self._get_json_params()
        elif self.base_tool.name == 'openclaw_tool':
            return self._get_openclaw_params()
        else:
            return [p for p in self.base_tool.parameters if p.name != "operation"]

    def _get_datetime_params(self) -> List[ToolParameter]:
        """Parameters accepted by a specific datetime operation."""
        if self.operation == "now":
            return [
                ToolParameter(
                    name="to_timezone",
                    type=ParameterType.STRING,
                    description="目标时区（如：UTC, Asia/Shanghai）",
                    required=False,
                    default="Asia/Shanghai"
                ),
                ToolParameter(
                    name="output_format",
                    type=ParameterType.STRING,
                    description="输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    required=False,
                    default="%Y-%m-%d %H:%M:%S"
                )
            ]
        elif self.operation == "format":
            return [
                ToolParameter(
                    name="input_value",
                    type=ParameterType.STRING,
                    description="输入值（时间字符串或时间戳）",
                    required=True
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
                )
            ]
        elif self.operation == "convert_timezone":
            return [
                ToolParameter(
                    name="input_value",
                    type=ParameterType.STRING,
                    description="输入值（时间字符串或时间戳）",
                    required=True
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
                )
            ]
        elif self.operation == "timestamp_to_datetime":
            return [
                ToolParameter(
                    name="input_value",
                    type=ParameterType.STRING,
                    description="输入值（时间字符串或时间戳）",
                    required=True
                ),
                ToolParameter(
                    name="output_format",
                    type=ParameterType.STRING,
                    description="输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    required=False,
                    default="%Y-%m-%d %H:%M:%S"
                ),
                ToolParameter(
                    name="to_timezone",
                    type=ParameterType.STRING,
                    description="目标时区（如：UTC, Asia/Shanghai）",
                    required=False,
                    default="Asia/Shanghai"
                )
            ]
        elif self.operation == "datetime_to_timestamp":
            return [
                ToolParameter(
                    name="input_value",
                    type=ParameterType.STRING,
                    description="输入值（时间字符串，如：2026-04-07 10:30:25）",
                    required=True
                ),
                ToolParameter(
                    name="input_format",
                    type=ParameterType.STRING,
                    description="输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                    required=False,
                    default="%Y-%m-%d %H:%M:%S"
                ),
                ToolParameter(
                    name="from_timezone",
                    type=ParameterType.STRING,
                    description="源时区（如：UTC, Asia/Shanghai）",
                    required=False,
                    default="Asia/Shanghai"
                )
            ]
        else:
            return []

    def _get_json_params(self) -> List[ToolParameter]:
        """Parameters accepted by a specific json operation."""
        base_params = [
            ToolParameter(
                name="input_data",
                type=ParameterType.STRING,
                description="输入数据（JSON字符串、YAML字符串或XML字符串）",
                required=True
            )
        ]

        if self.operation == "insert":
            return base_params + [
                ToolParameter(
                    name="json_path",
                    type=ParameterType.STRING,
                    description="JSON路径表达式（如：$.user.name或users[0].name）",
                    required=True
                ),
                ToolParameter(
                    name="new_value",
                    type=ParameterType.STRING,
                    description="新值（用于insert操作）",
                    required=True
                )
            ]
        elif self.operation == "replace":
            return base_params + [
                ToolParameter(
                    name="json_path",
                    type=ParameterType.STRING,
                    description="JSON路径表达式（如：$.user.name或users[0].name）",
                    required=True
                ),
                ToolParameter(
                    name="old_text",
                    type=ParameterType.STRING,
                    description="要替换的原文本（用于replace操作）",
                    required=True
                ),
                ToolParameter(
                    name="new_text",
                    type=ParameterType.STRING,
                    description="替换后的新文本（用于replace操作）",
                    required=True
                )
            ]
        elif self.operation == "delete":
            return base_params + [
                ToolParameter(
                    name="json_path",
                    type=ParameterType.STRING,
                    description="JSON路径表达式（如：$.user.name或users[0].name）",
                    required=True
                )
            ]
        elif self.operation == "parse":
            return base_params + [
                ToolParameter(
                    name="json_path",
                    type=ParameterType.STRING,
                    description="JSON路径表达式（如：$.user.name或users[0].name）",
                    required=True
                )
            ]
        else:
            return base_params

    def _get_openclaw_params(self) -> List[ToolParameter]:
        """Parameters accepted by a specific openclaw operation."""
        if self.operation == "print_task":
            return [
                ToolParameter(
                    name="message",
                    type=ParameterType.STRING,
                    description="发送给 OpenClaw 的打印任务描述，将用户的原始消息原封不动地传递给 OpenClaw，禁止改写、补充或润色用户的原文",
                    required=True
                ),
                ToolParameter(
                    name="image_url",
                    type=ParameterType.STRING,
                    description="可选，附带的设计图片或参考图，OpenClaw 可据此生成 3D 模型",
                    required=False
                )
            ]
        elif self.operation == "device_query":
            return [
                ToolParameter(
                    name="message",
                    type=ParameterType.STRING,
                    description="发送给 OpenClaw 的设备查询指令",
                    required=True
                )
            ]
        elif self.operation == "image_understand":
            return [
                ToolParameter(
                    name="message",
                    type=ParameterType.STRING,
                    description="发送给 OpenClaw 的图片理解任务，应描述需要对图片做什么（如描述内容、提取文字、分析信息）",
                    required=True
                ),
                ToolParameter(
                    name="image_url",
                    type=ParameterType.STRING,
                    description="要分析的图片 URL 或 base64 data URI",
                    required=False
                )
            ]
        else:
            return [
                ToolParameter(
                    name="message",
                    type=ParameterType.STRING,
                    description="发送给 OpenClaw Agent 的任务描述，应包含完整的任务需求",
                    required=True
                ),
                ToolParameter(
                    name="image_url",
                    type=ParameterType.STRING,
                    description="可选，附带的图片 URL 或 base64 data URI",
                    required=False
                )
            ]

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the bound operation on the wrapped tool."""
        kwargs["operation"] = self.operation
        return await self.base_tool.execute(**kwargs)
