"""MCP工具基类 - 整合版本"""
import time
from typing import List, Dict, Any

from app.models.tool_model import ToolType
from app.core.tools.base import BaseTool, ToolParameter, ToolResult, ParameterType
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class MCPTool(BaseTool):
    """MCP工具 - Model Context Protocol工具"""
    
    def __init__(self, tool_id: str, config: Dict[str, Any]):
        super().__init__(tool_id, config)
        self.server_url = config.get("server_url", "")
        self.connection_config = config.get("connection_config", {})
        self.available_tools = config.get("available_tools", [])
    
    @property
    def name(self) -> str:
        return f"mcp_tool_{self.tool_id[:8]}"
    
    @property
    def description(self) -> str:
        return f"MCP工具 - 连接到 {self.server_url}"
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.MCP
    
    @property
    def parameters(self) -> List[ToolParameter]:
        """根据工具名称返回对应参数"""
        # 如果有指定的工具名称，从 available_tools 中获取参数
        tool_name = getattr(self, '_current_tool_name', None)
        if tool_name and self.available_tools:
            for tool_info in self.available_tools:
                if tool_info.get("tool_name") == tool_name:
                    arguments = tool_info.get("arguments", {})
                    return self._generate_parameters_from_schema(arguments)
        
        # 默认返回通用参数
        return [
            ToolParameter(
                name="tool_name",
                type=ParameterType.STRING,
                description="要执行的工具名称",
                required=True
            ),
            ToolParameter(
                name="arguments",
                type=ParameterType.OBJECT,
                description="工具参数",
                required=False,
                default={}
            )
        ]
    
    def _generate_parameters_from_schema(self, arguments: Dict[str, Any]) -> List[ToolParameter]:
        """从参数schema生成参数列表"""
        properties = arguments.get("properties", {})
        required_fields = arguments.get("required", [])
        
        params = []
        for param_name, param_def in properties.items():
            param_type = self._convert_json_type_to_parameter_type(param_def.get("type", "string"))
            
            params.append(ToolParameter(
                name=param_name,
                type=param_type,
                description=param_def.get("description", f"参数: {param_name}"),
                required=param_name in required_fields,
                default=param_def.get("default"),
                enum=param_def.get("enum"),
                minimum=param_def.get("minimum"),
                maximum=param_def.get("maximum")
            ))
        
        return params
    
    def _convert_json_type_to_parameter_type(self, json_type: str) -> ParameterType:
        """转换JSON Schema类型到ParameterType"""
        type_mapping = {
            "string": ParameterType.STRING,
            "integer": ParameterType.INTEGER,
            "number": ParameterType.NUMBER,
            "boolean": ParameterType.BOOLEAN,
            "array": ParameterType.ARRAY,
            "object": ParameterType.OBJECT
        }
        return type_mapping.get(json_type, ParameterType.STRING)
    
    def set_current_tool(self, tool_name: str):
        """设置当前工具名称，用于获取特定参数"""
        self._current_tool_name = tool_name
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行MCP工具"""
        start_time = time.time()
        
        try:
            tool_name = kwargs.get("tool_name")
            if not tool_name:
                raise Exception("未指定工具名称")
            
            arguments = kwargs.get("arguments", {})
            
            from .client import SimpleMCPClient
            
            client = SimpleMCPClient(self.server_url, self.connection_config)
            
            async with client:
                result = await client.call_tool(tool_name, arguments)
                
                execution_time = time.time() - start_time
                return ToolResult.success_result(
                    data=result,
                    execution_time=execution_time
                )
                
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"MCP工具执行失败: {kwargs.get('tool_name', 'unknown')}, 错误: {e}")
            return ToolResult.error_result(
                error=str(e),
                error_code="MCP_EXECUTION_ERROR",
                execution_time=execution_time
            )


class MCPError(Exception):
    """MCP 错误基类"""
    pass


class MCPToolManager:
    """MCP 工具管理器 - 简化版本"""
    
    def __init__(self, db=None):
        self.db = db
        self._tool_cache: Dict[str, Dict[str, Any]] = {}  # server_url -> tools_info
    
    async def discover_tools(
        self, 
        server_url: str, 
        connection_config: Dict[str, Any] = None
    ) -> tuple[bool, List[Dict[str, Any]], str | None]:
        """发现 MCP 服务器上的工具"""
        try:
            from .client import SimpleMCPClient
            
            client = SimpleMCPClient(server_url, connection_config)
            
            async with client:
                tools = await client.list_tools()
                
                # 缓存工具信息
                self._tool_cache[server_url] = {
                    "tools": tools,
                    "connection_config": connection_config,
                    "last_updated": time.time()
                }
                
                logger.info(f"发现 {len(tools)} 个MCP工具: {server_url}")
                return True, tools, None
                
        except Exception as e:
            error_msg = f"发现工具失败: {e}"
            logger.error(error_msg)
            return False, [], error_msg
    
    async def test_tool_connection(
        self, 
        server_url: str, 
        connection_config: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """测试工具连接"""
        try:
            from .client import SimpleMCPClient
            
            client = SimpleMCPClient(server_url, connection_config)
            
            async with client:
                tools = await client.list_tools()
                
                return {
                    "success": True,
                    "tools_count": len(tools),
                    "tools": [tool.get("name") for tool in tools],
                    "message": "连接成功"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "连接失败"
            }