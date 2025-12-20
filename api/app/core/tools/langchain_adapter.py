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
    
    class Config:
        arbitrary_types_allowed = True
    
    def __init__(self, tool_instance: BaseTool, **kwargs):
        """初始化Langchain工具包装器
        
        Args:
            tool_instance: 内部工具实例
        """
        # 动态创建参数schema
        args_schema = LangchainAdapter._create_pydantic_schema(tool_instance.parameters)
        
        super().__init__(
            name=tool_instance.name,
            description=tool_instance.description,
            args_schema=args_schema,
            _tool_instance=tool_instance,
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
            # 执行内部工具
            result = await self._tool_instance.safe_execute(**kwargs)
            
            # 转换结果为Langchain格式
            return LangchainAdapter._format_result_for_langchain(result)
            
        except Exception as e:
            logger.error(f"工具执行失败: {self.name}, 错误: {e}")
            raise ToolException(f"工具执行失败: {str(e)}")


class LangchainAdapter:
    """Langchain适配器 - 负责工具格式转换和标准化"""
    
    @staticmethod
    def convert_tool(tool: BaseTool) -> LangchainToolWrapper:
        """将内部工具转换为Langchain工具
        
        Args:
            tool: 内部工具实例
            
        Returns:
            Langchain兼容的工具包装器
        """
        try:
            wrapper = LangchainToolWrapper(tool_instance=tool)
            logger.debug(f"工具转换成功: {tool.name} -> Langchain格式")
            return wrapper
            
        except Exception as e:
            logger.error(f"工具转换失败: {tool.name}, 错误: {e}")
            raise
    
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
        
        logger.info(f"批量转换完成: {len(converted_tools)}/{len(tools)} 个工具")
        return converted_tools
    
    @staticmethod
    def _create_pydantic_schema(parameters: List[ToolParameter]) -> Type[BaseModel]:
        """根据工具参数创建Pydantic schema
        
        Args:
            parameters: 工具参数列表
            
        Returns:
            Pydantic模型类
        """
        # 构建字段定义
        fields = {}
        annotations = {}
        
        for param in parameters:
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
                field_kwargs["regex"] = f"^({'|'.join(map(str, param.enum))})$"
            
            if param.minimum is not None:
                field_kwargs["ge"] = param.minimum
            
            if param.maximum is not None:
                field_kwargs["le"] = param.maximum
            
            if param.pattern:
                field_kwargs["regex"] = param.pattern
            
            fields[param.name] = Field(**field_kwargs)
            annotations[param.name] = python_type
        
        # 动态创建Pydantic模型
        schema_class = type(
            "ToolArgsSchema",
            (BaseModel,),
            {
                "__annotations__": annotations,
                **fields,
                "Config": type("Config", (), {"extra": "forbid"})
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