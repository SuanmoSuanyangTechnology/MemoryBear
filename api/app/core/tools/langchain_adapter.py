"""Langchain适配器 - 将工具转换为langchain兼容格式"""
import json
from typing import Dict, Any, List, Optional, Type
from pydantic import BaseModel, Field
from langchain.tools import BaseTool as LangchainBaseTool
from langchain_core.tools import ToolException

from app.core.tools.base import BaseTool, ToolResult, ToolParameter, ParameterType
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class LangchainToolWrapper(LangchainBaseTool):
    """Langchain工具包装器"""
    
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    args_schema: Optional[Type[BaseModel]] = Field(None, description="参数schema")
    return_direct: bool = Field(False, description="是否直接返回结果")
    
    # 内部工具实例
    tool_instance: BaseTool = Field(..., description="内部工具实例")
    # 特定操作（用于自定义工具）
    operation: Optional[str] = Field(None, description="特定操作")
    
    class Config:
        arbitrary_types_allowed = True
    
    def __init__(self, tool_instance: BaseTool, operation: Optional[str] = None, **kwargs):
        """初始化Langchain工具包装器
        
        Args:
            tool_instance: 内部工具实例
            operation: 特定操作（用于自定义工具）
        """
        # 动态创建参数schema
        args_schema = LangchainAdapter._create_pydantic_schema(
            tool_instance.parameters, operation
        )
        
        # 构建工具名称
        tool_name = tool_instance.name
        if operation:
            tool_name = f"{tool_instance.name}_{operation}"
        
        super().__init__(
            name=tool_name,
            description=tool_instance.description,
            args_schema=args_schema,
            tool_instance=tool_instance,
            operation=operation,
            **kwargs
        )
    
    def _run(
        self,
        run_manager=None,
        **kwargs: Any,
    ) -> str:
        """同步执行工具（Langchain要求）"""
        # 由于我们的工具是异步的，这里抛出异常提示使用异步版本
        raise NotImplementedError("请使用 _arun 方法进行异步调用")
    
    async def _arun(
        self,
        run_manager=None,
        **kwargs: Any,
    ) -> str:
        """异步执行工具"""
        try:
            # 如果有特定操作，添加到参数中
            if self.operation:
                kwargs["operation"] = self.operation
            
            # 执行内部工具
            result = await self.tool_instance.safe_execute(**kwargs)
            
            # 转换结果为Langchain格式
            return LangchainAdapter._format_result_for_langchain(result)
            
        except Exception as e:
            logger.error(f"工具执行失败: {self.name}, 错误: {e}")
            raise ToolException(f"工具执行失败: {str(e)}")


class LangchainAdapter:
    """Langchain适配器 - 负责工具格式转换和标准化"""
    
    @staticmethod
    def convert_tool(tool: BaseTool, operation: Optional[str] = None) -> LangchainToolWrapper:
        """将内部工具转换为Langchain工具
        
        Args:
            tool: 内部工具实例
            operation: 特定操作（适用于有操作的工具）或MCP工具名称
            
        Returns:
            Langchain兼容的工具包装器
        """
        try:
            # 处理MCP工具的特定工具名称
            if hasattr(tool, 'tool_type') and tool.tool_type.value == "mcp" and operation:
                # 为MCP工具创建特定工具名称的实例
                mcp_tool = LangchainAdapter._create_mcp_tool_with_name(tool, operation)
                wrapper = LangchainToolWrapper(tool_instance=mcp_tool)
                logger.debug(f"MCP工具转换成功: {tool.name}_{operation} -> Langchain格式")
                return wrapper
            elif operation and LangchainAdapter._tool_supports_operations(tool):
                # 为支持多操作的工具创建特定操作实例
                if tool.tool_type.value == "custom":
                    # 自定义工具直接传递operation参数
                    wrapper = LangchainToolWrapper(tool_instance=tool, operation=operation)
                else:
                    # 内置工具使用OperationTool包装
                    operation_tool = LangchainAdapter._create_operation_tool(tool, operation)
                    wrapper = LangchainToolWrapper(tool_instance=operation_tool)
                logger.debug(f"工具转换成功: {tool.name}_{operation} -> Langchain格式")
                return wrapper
            else:
                # 单个工具
                wrapper = LangchainToolWrapper(tool_instance=tool)
                logger.debug(f"工具转换成功: {tool.name} -> Langchain格式")
                return wrapper
            
        except Exception as e:
            logger.error(f"工具转换失败: {tool.name}, 错误: {e}")
            raise
    
    @staticmethod
    def _tool_supports_operations(tool: BaseTool) -> bool:
        """检查工具是否支持多操作"""
        # 内置工具中支持操作的工具
        builtin_operation_tools = ['datetime_tool', 'json_tool']
        
        # 检查内置工具
        if tool.tool_type.value == "builtin" and tool.name in builtin_operation_tools:
            return True
        
        # 检查自定义工具（自定义工具通过解析OpenAPI schema支持多操作）
        if tool.tool_type.value == "custom":
            # 检查工具是否有多个操作
            if hasattr(tool, '_parsed_operations') and len(tool._parsed_operations) > 1:
                return True
            # 或者检查参数中是否有operation参数
            for param in tool.parameters:
                if param.name == "operation" and param.enum:
                    return True
        
        return False
    
    @staticmethod
    def _create_operation_tool(base_tool: BaseTool, operation: str) -> BaseTool:
        """为特定操作创建工具实例"""
        if base_tool.tool_type.value == "builtin":
            from app.core.tools.builtin.operation_tool import OperationTool
            return OperationTool(base_tool, operation)
        else:
            raise ValueError(f"不支持的工具类型: {base_tool.tool_type.value}")
    
    @staticmethod
    def _create_mcp_tool_with_name(mcp_tool: BaseTool, tool_name: str) -> BaseTool:
        """为MCP工具创建指定工具名称的实例"""
        mcp_tool.set_current_tool(tool_name)
        return mcp_tool

    @staticmethod
    def convert_tools(tools: List[BaseTool]) -> List[LangchainToolWrapper]:
        """批量转换工具
        
        Args:
            tools: 工具列表
            
        Returns:
            Langchain工具列表
        """
        converted_tools = []
        
        for tool in tools:
            try:
                converted_tool = LangchainAdapter.convert_tool(tool)
                converted_tools.append(converted_tool)
            except Exception as e:
                logger.error(f"跳过工具转换: {tool.name}, 错误: {e}")
        
        logger.info(f"批量转换完成: {len(converted_tools)} 个工具")
        return converted_tools
    
    @staticmethod
    def _create_pydantic_schema(
        parameters: List[ToolParameter], 
        operation: Optional[str] = None
    ) -> Type[BaseModel]:
        """根据工具参数创建Pydantic schema
        
        Args:
            parameters: 工具参数列表
            operation: 特定操作（用于过滤参数）
            
        Returns:
            Pydantic模型类
        """
        # 构建字段定义
        fields = {}
        annotations = {}
        
        # 如果指定了operation，过滤掉operation参数
        filtered_params = parameters
        if operation:
            filtered_params = [p for p in parameters if p.name != "operation"]
        
        for param in filtered_params:
            # 确定Python类型
            python_type = LangchainAdapter._get_python_type(param.type)
            
            # 处理可选参数
            if not param.required:
                python_type = Optional[python_type]
            
            # 创建Field定义
            field_kwargs = {
                "description": param.description
            }
            
            if param.default is not None:
                field_kwargs["default"] = param.default
            elif not param.required:
                field_kwargs["default"] = None
            else:
                field_kwargs["default"] = ...  # 必需字段
            
            # 添加验证约束
            if param.enum:
                # 枚举值约束
                field_kwargs["pattern"] = f"^({'|'.join(map(str, param.enum))})$"
            
            if param.minimum is not None:
                field_kwargs["ge"] = param.minimum
            
            if param.maximum is not None:
                field_kwargs["le"] = param.maximum
            
            if param.pattern:
                field_kwargs["pattern"] = param.pattern
            
            fields[param.name] = Field(**field_kwargs)
            annotations[param.name] = python_type
        
        # 动态创建Pydantic模型
        schema_class = type(
            "ToolArgsSchema",
            (BaseModel,),
            {
                "__module__": __name__,
                "__annotations__": annotations,
                "model_config": {"extra": "forbid"},
                **fields
            }
        )
        
        return schema_class
    
    @staticmethod
    def _get_python_type(param_type: ParameterType) -> type:
        """获取参数类型对应的Python类型
        
        Args:
            param_type: 参数类型
            
        Returns:
            Python类型
        """
        type_mapping = {
            ParameterType.STRING: str,
            ParameterType.INTEGER: int,
            ParameterType.NUMBER: float,
            ParameterType.BOOLEAN: bool,
            ParameterType.ARRAY: list,
            ParameterType.OBJECT: dict
        }
        
        return type_mapping.get(param_type, str)
    
    @staticmethod
    def _format_result_for_langchain(result: ToolResult) -> str:
        """将工具结果格式化为Langchain标准格式
        
        Args:
            result: 工具执行结果
            
        Returns:
            格式化的字符串结果
        """
        if not result.success:
            # 错误结果
            error_info = {
                "success": False,
                "error": result.error,
                "error_code": result.error_code,
                "execution_time": result.execution_time
            }
            return json.dumps(error_info, ensure_ascii=False, indent=2)
        
        # 成功结果
        if isinstance(result.data, str):
            # 如果数据已经是字符串，直接返回
            return result.data
        elif isinstance(result.data, (dict, list)):
            # 如果是结构化数据，转换为JSON
            return json.dumps(result.data, ensure_ascii=False, indent=2)
        else:
            # 其他类型转换为字符串
            return str(result.data)
    
    @staticmethod
    def create_tool_description(tool: BaseTool) -> Dict[str, Any]:
        """创建工具描述（用于工具发现和文档生成）
        
        Args:
            tool: 工具实例
            
        Returns:
            工具描述字典
        """
        return {
            "name": tool.name,
            "description": tool.description,
            "tool_type": tool.tool_type.value,
            "version": tool.version,
            "status": tool.status.value,
            "tags": tool.tags,
            "parameters": [
                {
                    "name": param.name,
                    "type": param.type.value,
                    "description": param.description,
                    "required": param.required,
                    "default": param.default,
                    "enum": param.enum,
                    "minimum": param.minimum,
                    "maximum": param.maximum,
                    "pattern": param.pattern
                }
                for param in tool.parameters
            ],
            "langchain_compatible": True
        }
    
    @staticmethod
    def validate_langchain_compatibility(tool: BaseTool) -> tuple[bool, List[str]]:
        """验证工具是否与Langchain兼容
        
        Args:
            tool: 工具实例
            
        Returns:
            (是否兼容, 问题列表)
        """
        issues = []
        
        # 检查工具名称
        if not tool.name or not isinstance(tool.name, str):
            issues.append("工具名称必须是非空字符串")
        
        # 检查工具描述
        if not tool.description or not isinstance(tool.description, str):
            issues.append("工具描述必须是非空字符串")
        
        # 检查参数定义
        for param in tool.parameters:
            if not param.name or not isinstance(param.name, str):
                issues.append(f"参数名称无效: {param.name}")
            
            if param.type not in ParameterType:
                issues.append(f"不支持的参数类型: {param.type}")
            
            if param.required and param.default is not None:
                issues.append(f"必需参数不应有默认值: {param.name}")
        
        # 检查是否有execute方法
        if not hasattr(tool, 'execute') or not callable(getattr(tool, 'execute')):
            issues.append("工具必须实现execute方法")
        
        return len(issues) == 0, issues
    
    @staticmethod
    def get_langchain_tool_schema(tool: BaseTool) -> Dict[str, Any]:
        """获取Langchain工具的OpenAPI schema
        
        Args:
            tool: 工具实例
            
        Returns:
            OpenAPI schema字典
        """
        # 构建参数schema
        properties = {}
        required = []
        
        for param in tool.parameters:
            prop_schema = {
                "type": LangchainAdapter._get_openapi_type(param.type),
                "description": param.description
            }
            
            if param.enum:
                prop_schema["enum"] = param.enum
            
            if param.minimum is not None:
                prop_schema["minimum"] = param.minimum
            
            if param.maximum is not None:
                prop_schema["maximum"] = param.maximum
            
            if param.pattern:
                prop_schema["pattern"] = param.pattern
            
            if param.default is not None:
                prop_schema["default"] = param.default
            
            properties[param.name] = prop_schema
            
            if param.required:
                required.append(param.name)
        
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
    
    @staticmethod
    def _get_openapi_type(param_type: ParameterType) -> str:
        """获取OpenAPI类型
        
        Args:
            param_type: 参数类型
            
        Returns:
            OpenAPI类型字符串
        """
        type_mapping = {
            ParameterType.STRING: "string",
            ParameterType.INTEGER: "integer",
            ParameterType.NUMBER: "number",
            ParameterType.BOOLEAN: "boolean",
            ParameterType.ARRAY: "array",
            ParameterType.OBJECT: "object"
        }
        
        return type_mapping.get(param_type, "string")