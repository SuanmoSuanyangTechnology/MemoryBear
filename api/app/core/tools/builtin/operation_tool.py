"""操作工具 - 为特定操作创建的工具包装器"""
from typing import List
from app.core.tools.base import BaseTool, ToolParameter, ToolResult, ParameterType
from app.models import ToolType


class OperationTool(BaseTool):
    """操作工具 - 包装基础工具的特定操作"""
    
    def __init__(self, base_tool: BaseTool, operation: str):
        self.base_tool = base_tool
        self.operation = operation
        super().__init__(base_tool.tool_id, base_tool.config)
    
    @property
    def name(self) -> str:
        return f"{self.base_tool.name}_{self.operation}"

    @property
    def tool_type(self) -> ToolType:
        """工具类型"""
        return ToolType.BUILTIN
    
    @property
    def description(self) -> str:
        return f"{self.base_tool.description} - {self.operation}"
    
    @property
    def parameters(self) -> List[ToolParameter]:
        """返回特定操作的参数"""
        if self.base_tool.name == 'datetime_tool':
            return self._get_datetime_params()
        elif self.base_tool.name == 'json_tool':
            return self._get_json_params()
        else:
            # 默认返回除operation外的所有参数
            return [p for p in self.base_tool.parameters if p.name != "operation"]
    
    def _get_datetime_params(self) -> List[ToolParameter]:
        """获取datetime_tool特定操作的参数"""
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
        else:
            return []
    
    def _get_json_params(self) -> List[ToolParameter]:
        """获取json_tool特定操作的参数"""
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
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行特定操作"""
        # 添加operation参数
        kwargs["operation"] = self.operation
        return await self.base_tool.execute(**kwargs)