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
        self._current_tool_name: str | None = None  # 当前工具名称
    
    @property
    def tool_type(self) -> ToolType:
        return ToolType.MCP
    
    def _get_tool_info(self, tool_name: str) -> Dict[str, Any] | None:
        """从available_tools中获取指定工具的信息"""
        if not self.available_tools or not tool_name:
            return None
        
        for tool_item in self.available_tools:
            if tool_name in tool_item:
                return tool_item[tool_name]
        return None
    
    @property
    def name(self) -> str:
        # 返回当前工具名称，如果没有设置则返回默认名称
        if self._current_tool_name:
            return self._current_tool_name
        return f"mcp_tool_{self.tool_id[:8]}"
    
    @property
    def description(self) -> str:
        # 从 available_tools 中获取当前工具的描述
        if self._current_tool_name and self.available_tools:
            tool_info = self._get_tool_info(self._current_tool_name)
            if tool_info:
                return tool_info.get("description", f"MCP工具 - {self._current_tool_name}")
        return f"MCP工具 - 连接到 {self.server_url}"
    
    @property
    def parameters(self) -> List[ToolParameter]:
        """根据工具名称返回对应参数"""
        tool_name = getattr(self, '_current_tool_name', self.name)
        tool_info = self._get_tool_info(tool_name)
        
        if tool_info:
            input_schema = tool_info.get("inputSchema", {})
            if input_schema:
                return self._generate_parameters_from_schema(input_schema)

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
    
    def _generate_parameters_from_schema(self, input_schema: Dict[str, Any]) -> List[ToolParameter]:
        """从inputSchema生成参数列表"""
        properties = input_schema.get("properties", {})
        required_fields = input_schema.get("required", [])
        
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
            # 确定要调用的工具名称
            # 优先使用参数中的 tool_name，否则使用当前工具名（从 available_tools 提取）
            tool_name = kwargs.pop("tool_name", None) or self.name
            arguments = kwargs.pop("arguments", kwargs)  # 剩余参数作为工具参数
            
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
                "error": "连接失败",
                "message": str(e)
            }