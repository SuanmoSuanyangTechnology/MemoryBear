"""Langchain adapter - converts tools into a langchain-compatible format"""
import logging

import json
from typing import Any, Dict, Iterable, List, Optional, Type
from pydantic import BaseModel, Field
from langchain.tools import BaseTool as LangchainBaseTool
from langchain_core.tools import ToolException

from ..models.tool_model import ToolType
from .base import BaseTool, ToolResult, ToolParameter, ParameterType
from .selection import normalize_tool_selection
from .serialization import serialize_tool_parameter

logger = logging.getLogger(__name__)


def format_tool_argument_error(error: Exception) -> str:
    """Turn an argument-validation failure into a tool result the model can act on.

    LangChain validates arguments before the tool body runs. Without a handler the
    failure surfaces only as a raw error event (the model gets no tool output and the
    UI shows an empty result). Returning this text as the tool result lets the model
    see exactly what was wrong and retry.
    """
    missing: List[str] = []
    invalid: List[str] = []
    try:
        for item in error.errors():  # type: ignore[attr-defined]
            loc = ".".join(str(p) for p in item.get("loc", ())) or "arguments"
            if item.get("type") in ("missing", "value_error.missing"):
                missing.append(loc)
            else:
                invalid.append(f"{loc}: {item.get('msg', 'invalid value')}")
    except Exception:  # noqa: BLE001 - never let error formatting fail the call
        invalid.append(str(error)[:300])

    parts: List[str] = []
    if missing:
        parts.append(f"缺少必填参数: {', '.join(missing)}")
    if invalid:
        parts.append("参数不合法: " + "; ".join(invalid))
    hint = "请补全参数后重新调用。"
    if missing:
        hint += (
            "如果上一次调用的内容很长，很可能是输出达到长度上限被截断了，排在长内容之后的必填参数因此丢失："
            "请把必填的短参数（如 path）写在最前面，并把长内容拆成多次较小的调用（例如先写骨架，再分段追加）。"
        )
    return json.dumps(
        {
            "success": False,
            "error": "工具参数校验失败，工具没有被执行。" + " ".join(parts) + " " + hint,
            "error_code": "INVALID_ARGUMENTS",
        },
        ensure_ascii=False,
        indent=2,
    )


def _operation_enum(tool: BaseTool) -> Optional[List[str]]:
    """Return the tool's operation choices, when it declares an operation parameter."""
    for param in tool.parameters:
        if param.name == "operation" and param.enum:
            return list(param.enum)
    return None


def _mcp_cached_tool_names(tool: BaseTool) -> List[str]:
    """Return the MCP method names from the instance's cached ``available_tools``."""
    names: List[str] = []
    for item in getattr(tool, "available_tools", None) or []:
        if isinstance(item, dict):
            candidates = [name for name in item.keys() if name]
        elif isinstance(item, str):
            candidates = [item]
        else:
            continue
        for name in candidates:
            if name not in names:
                names.append(name)
    return names


def _custom_operations(tool: BaseTool) -> List[str]:
    """Return the operation ids parsed from a custom tool's OpenAPI schema."""
    parsed = getattr(tool, "_parsed_operations", None)
    if isinstance(parsed, dict):
        return [name for name in parsed.keys() if name]
    return []


def _operation_bindable(
    tool: BaseTool,
    tool_type: str,
    operation: str,
    enum: Optional[List[str]],
) -> bool:
    """Whether a stored operation still exists on the runtime tool instance."""
    if tool_type == ToolType.MCP.value:
        names = _mcp_cached_tool_names(tool)
        return bool(names) and operation in names
    if tool_type == ToolType.CUSTOM.value:
        operations = _custom_operations(tool)
        return bool(operations) and operation in operations
    if tool_type == ToolType.BUILTIN.value:
        return enum is None or operation in enum
    return True


def expand_tool_selection(tool: BaseTool, operation: Optional[str] = None) -> List[Optional[str]]:
    """Expand one saved tool selection into the operations bound at runtime.

    Runtime plane only: reads the tool instance in memory and never queries the
    database or asks an MCP server for its live method list. ``[None]`` means a
    whole-tool wrapper; an empty list means the selection cannot run and the
    caller must skip it.
    """
    tool_type = tool.tool_type.value
    enum = _operation_enum(tool)
    normalized = normalize_tool_selection(
        tool_type, operation, has_operation_enum=enum is not None
    )

    if tool_type == ToolType.WORKFLOW.value:
        return [None]

    if normalized:
        if not _operation_bindable(tool, tool_type, normalized, enum):
            logger.warning(
                "tool_selection_unavailable tool_id=%s tool_type=%s operation=%s reason=operation_not_found",
                tool.tool_id, tool_type, normalized,
            )
            return []
        return [normalized]

    if tool_type == ToolType.MCP.value:
        names = _mcp_cached_tool_names(tool)
        if not names:
            logger.warning(
                "tool_selection_unavailable tool_id=%s tool_type=%s reason=mcp_methods_cache_empty",
                tool.tool_id, tool_type,
            )
        return names
    if tool_type == ToolType.CUSTOM.value:
        operations = _custom_operations(tool)
        if not operations:
            logger.warning(
                "tool_selection_unavailable tool_id=%s tool_type=%s reason=custom_operations_empty",
                tool.tool_id, tool_type,
            )
            return []
        return operations if len(operations) > 1 else [None]
    if tool_type == ToolType.BUILTIN.value and enum:
        return list(enum)
    return [None]


def plan_tool_selections(
    selections: Iterable[tuple[Any, Optional[str]]],
) -> List[tuple[str, Optional[str]]]:
    """Normalize a tool list before expansion.

    A whole-tool selection (``operation=None``) suppresses every method-level
    entry for the same tool, regardless of order; duplicate
    ``(tool_id, operation)`` pairs keep the first occurrence.
    """
    entries: List[tuple[str, Optional[str]]] = []
    for tool_id, operation in selections:
        normalized_id = str(tool_id).strip() if tool_id else ""
        if not normalized_id:
            continue
        entries.append((normalized_id, operation or None))

    whole_tools = {tool_id for tool_id, operation in entries if operation is None}
    planned: List[tuple[str, Optional[str]]] = []
    seen: set[tuple[str, Optional[str]]] = set()
    for tool_id, operation in entries:
        if operation is not None and tool_id in whole_tools:
            logger.debug("二级工具选择覆盖三级条目: tool_id=%s operation=%s", tool_id, operation)
            continue
        key = (tool_id, operation)
        if key in seen:
            continue
        seen.add(key)
        planned.append(key)
    return planned


class LangchainToolWrapper(LangchainBaseTool):
    """Langchain tool wrapper"""
    
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    args_schema: Optional[Type[BaseModel]] = Field(None, description="参数schema")
    return_direct: bool = Field(False, description="是否直接返回结果")
    
    # Internal tool instance
    tool_instance: BaseTool = Field(..., description="内部工具实例")
    # Specific operation (for custom tools)
    operation: Optional[str] = Field(None, description="特定操作")
    
    class Config:
        arbitrary_types_allowed = True
    
    def __init__(self, tool_instance: BaseTool, operation: Optional[str] = None, **kwargs):
        """Initialize the Langchain tool wrapper
        
        Args:
            tool_instance: Internal tool instance
            operation: Specific operation (for custom tools)
        """
        # Dynamically create the argument schema for the bound operation
        args_schema = LangchainAdapter._create_pydantic_schema(
            tool_instance.parameters_for_operation(operation), operation
        )
        
        # Build the tool name
        tool_name = tool_instance.name
        if operation:
            tool_name = f"{tool_instance.name}_{operation}"
        
        super().__init__(
            name=tool_name,
            description=tool_instance.description,
            args_schema=args_schema,
            tool_instance=tool_instance,
            operation=operation,
            handle_validation_error=format_tool_argument_error,
            **kwargs
        )
    
    def _run(
        self,
        run_manager=None,
        **kwargs: Any,
    ) -> str:
        """Execute the tool synchronously (required by Langchain)"""
        # Our tools are asynchronous, so raise to point callers at the async variant
        raise NotImplementedError("请使用 _arun 方法进行异步调用")
    
    async def _arun(
        self,
        run_manager=None,
        **kwargs: Any,
    ) -> str:
        """Execute the tool asynchronously"""
        try:
            # Attach the operation to the arguments when present
            if self.operation:
                kwargs["operation"] = self.operation
            
            # Execute the wrapped tool
            result = await self.tool_instance.safe_execute(**kwargs)
            
            # Convert the result to Langchain format
            return LangchainAdapter._format_result_for_langchain(result)
            
        except Exception as e:
            # Do not raise: an exception here aborts the whole agent turn and the
            # model never sees why. Hand the failure back as the tool result instead.
            logger.error(f"工具执行失败: {self.name}, 错误: {e}", exc_info=True)
            return json.dumps(
                {
                    "success": False,
                    "error": f"工具执行失败: {str(e) or type(e).__name__}",
                    "error_code": "EXECUTION_ERROR",
                },
                ensure_ascii=False,
                indent=2,
            )


class LangchainAdapter:
    """Langchain adapter - handles tool format conversion and normalization"""
    
    @staticmethod
    def convert_tool(tool: BaseTool, operation: Optional[str] = None) -> LangchainToolWrapper:
        """Convert an internal tool into a Langchain tool
        
        Args:
            tool: Internal tool instance
            operation: Specific operation (for tools that support operations) or MCP tool name
            
        Returns:
            Langchain-compatible tool wrapper
        """
        try:
            # Handle operation-bound names for MCP tools
            if hasattr(tool, 'tool_type') and tool.tool_type.value == "mcp" and operation:
                # Create an MCP tool instance bound to the specific tool name
                mcp_tool = LangchainAdapter._create_mcp_tool_with_name(tool, operation)
                wrapper = LangchainToolWrapper(tool_instance=mcp_tool)
                logger.debug(f"MCP工具转换成功: {tool.name}_{operation} -> Langchain格式")
                return wrapper
            elif operation and LangchainAdapter._tool_supports_operations(tool):
                # Create an operation-specific instance for tools supporting multiple operations
                if tool.tool_type.value == "custom":
                    # Custom tools receive the operation argument directly
                    wrapper = LangchainToolWrapper(tool_instance=tool, operation=operation)
                else:
                    # Built-in tools are wrapped with OperationTool
                    operation_tool = LangchainAdapter._create_operation_tool(tool, operation)
                    wrapper = LangchainToolWrapper(tool_instance=operation_tool)
                logger.debug(f"工具转换成功: {tool.name}_{operation} -> Langchain格式")
                return wrapper
            else:
                # Single tool
                wrapper = LangchainToolWrapper(tool_instance=tool)
                logger.debug(f"工具转换成功: {tool.name} -> Langchain格式")
                return wrapper
            
        except Exception as e:
            logger.error(f"工具转换失败: {tool.name}, 错误: {e}")
            raise
    
    @staticmethod
    def _tool_supports_operations(tool: BaseTool) -> bool:
        """Check whether a tool supports multiple operations"""
        # Built-in tools that support operations
        builtin_operation_tools = ['datetime_tool', 'json_tool', 'openclaw_tool']
        
        # Check built-in tools
        if tool.tool_type.value == "builtin" and tool.name in builtin_operation_tools:
            return True
        
        # Check custom tools (they gain multi-operation support via OpenAPI schema parsing)
        if tool.tool_type.value == "custom":
            # Check whether the tool exposes multiple operations
            if hasattr(tool, '_parsed_operations') and len(tool._parsed_operations) > 1:
                return True
            # Or check for a declared operation parameter
            for param in tool.parameters:
                if param.name == "operation" and param.enum:
                    return True
        
        return False
    
    @staticmethod
    def _create_operation_tool(base_tool: BaseTool, operation: str) -> BaseTool:
        """Create a tool instance for a specific operation"""
        if base_tool.tool_type.value == "builtin":
            from .builtin.operation_tool import OperationTool
            return OperationTool(base_tool, operation)
        else:
            raise ValueError(f"不支持的工具类型: {base_tool.tool_type.value}")
    
    @staticmethod
    def _create_mcp_tool_with_name(mcp_tool: BaseTool, tool_name: str) -> BaseTool:
        """Create an MCP tool instance bound to the given tool name"""
        # Create a fresh instance per tool to avoid a shared _current_tool_name being overwritten
        import copy
        new_tool = copy.deepcopy(mcp_tool)
        new_tool.set_current_tool(tool_name)
        return new_tool

    @staticmethod
    def convert_selection(
        tool: BaseTool, operation: Optional[str] = None
    ) -> List[LangchainToolWrapper]:
        """Expand one saved selection and convert every expanded operation."""
        return [
            LangchainAdapter.convert_tool(tool, expanded)
            for expanded in expand_tool_selection(tool, operation)
        ]

    @staticmethod
    def convert_tools(tools: List[BaseTool]) -> List[LangchainToolWrapper]:
        """Convert multiple tools at once
        
        Args:
            tools: List of tools
            
        Returns:
            List of Langchain tools
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
        """Create a Pydantic schema from the tool parameters
        
        Args:
            parameters: List of tool parameters
            operation: Specific operation (used to filter parameters)
            
        Returns:
            Pydantic model class
        """
        # Build field definitions
        fields = {}
        annotations = {}
        
        # Drop the operation parameter when a specific operation is given
        filtered_params = parameters
        if operation:
            filtered_params = [p for p in parameters if p.name != "operation"]
        
        for param in filtered_params:
            # Resolve the Python type
            python_type = LangchainAdapter._get_python_type(param.type)
            
            # Handle optional parameters
            if not param.required:
                python_type = Optional[python_type]
            
            # Create the Field definition
            field_kwargs = {
                "description": param.description
            }
            
            if param.default is not None:
                field_kwargs["default"] = param.default
            elif not param.required:
                field_kwargs["default"] = None
            else:
                field_kwargs["default"] = ...  # Required field
            
            # Add validation constraints
            if param.enum:
                # Enum constraint
                field_kwargs["pattern"] = f"^({'|'.join(map(str, param.enum))})$"
            
            if param.minimum is not None:
                field_kwargs["ge"] = param.minimum
            
            if param.maximum is not None:
                field_kwargs["le"] = param.maximum
            
            if param.pattern:
                field_kwargs["pattern"] = param.pattern
            
            fields[param.name] = Field(**field_kwargs)
            annotations[param.name] = python_type
        
        # Dynamically create the Pydantic model
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
        """Get the Python type for a parameter type
        
        Args:
            param_type: Parameter type
            
        Returns:
            Python type
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
        """Format a tool result into the standard Langchain format
        
        Args:
            result: Tool execution result
            
        Returns:
            Formatted string result
        """
        if not result.success:
            # Error result
            error_info = {
                "success": False,
                "error": result.error,
                "error_code": result.error_code,
                "execution_time": result.execution_time
            }
            return json.dumps(error_info, ensure_ascii=False, indent=2)
        
        # Success result
        if isinstance(result.data, str):
            # Return string data as-is
            return result.data
        elif isinstance(result.data, (dict, list)):
            # Serialize structured data as JSON
            return json.dumps(result.data, ensure_ascii=False, indent=2)
        else:
            # Convert other types to a string
            return str(result.data)
    
    @staticmethod
    def create_tool_description(tool: BaseTool) -> Dict[str, Any]:
        """Build a tool description (for tool discovery and documentation)
        
        Args:
            tool: Tool instance
            
        Returns:
            Tool description dictionary
        """
        return {
            "name": tool.name,
            "description": tool.description,
            "tool_type": tool.tool_type.value,
            "version": tool.version,
            "status": tool.status.value,
            "tags": tool.tags,
            "parameters": [
                serialize_tool_parameter(param)
                for param in tool.parameters
            ],
            "langchain_compatible": True
        }
    
    @staticmethod
    def validate_langchain_compatibility(tool: BaseTool) -> tuple[bool, List[str]]:
        """Validate whether a tool is Langchain-compatible
        
        Args:
            tool: Tool instance
            
        Returns:
            (is_compatible, issues)
        """
        issues = []
        
        # Check the tool name
        if not tool.name or not isinstance(tool.name, str):
            issues.append("工具名称必须是非空字符串")
        
        # Check the tool description
        if not tool.description or not isinstance(tool.description, str):
            issues.append("工具描述必须是非空字符串")
        
        # Check parameter definitions
        for param in tool.parameters:
            if not param.name or not isinstance(param.name, str):
                issues.append(f"参数名称无效: {param.name}")
            
            if param.type not in ParameterType:
                issues.append(f"不支持的参数类型: {param.type}")
            
            if param.required and param.default is not None:
                issues.append(f"必需参数不应有默认值: {param.name}")
        
        # Check for an execute method
        if not hasattr(tool, 'execute') or not callable(getattr(tool, 'execute')):
            issues.append("工具必须实现execute方法")
        
        return len(issues) == 0, issues
    
    @staticmethod
    def get_langchain_tool_schema(tool: BaseTool) -> Dict[str, Any]:
        """Get the OpenAPI schema of a Langchain tool
        
        Args:
            tool: Tool instance
            
        Returns:
            OpenAPI schema dictionary
        """
        # Build the parameter schema
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
        """Get the OpenAPI type
        
        Args:
            param_type: Parameter type
            
        Returns:
            OpenAPI type string
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
