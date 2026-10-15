"""MCP tool base class - consolidated version."""
import logging
import time
from typing import List, Dict, Any

from ...models.tool_model import ToolType
from ..base import BaseTool, ToolParameter, ParameterType, ToolResult

logger = logging.getLogger(__name__)


class MCPTool(BaseTool):
    """MCP tool - Model Context Protocol tool."""

    def __init__(self, tool_id: str, config: Dict[str, Any]):
        super().__init__(tool_id, config)
        self.server_url = config.get("server_url", "")
        self.connection_config = config.get("connection_config", {})
        self.available_tools = config.get("available_tools", [])
        self._current_tool_name: str | None = None  # current tool name

    @property
    def tool_type(self) -> ToolType:
        return ToolType.MCP

    def _get_tool_info(self, tool_name: str) -> Dict[str, Any] | None:
        """Get the info of the given tool from available_tools."""
        if not self.available_tools or not tool_name:
            return None

        for tool_item in self.available_tools:
            if tool_name in tool_item:
                return tool_item[tool_name]
        return None

    @property
    def name(self) -> str:
        # Return the current tool name, or a default name when unset
        if self._current_tool_name:
            return self._current_tool_name
        return f"mcp_tool_{self.tool_id[:8]}"

    @property
    def description(self) -> str:
        # Get the current tool's description from available_tools
        if self._current_tool_name and self.available_tools:
            tool_info = self._get_tool_info(self._current_tool_name)
            if tool_info:
                return tool_info.get("description", f"MCP工具 - {self._current_tool_name}")
        return f"MCP工具 - 连接到 {self.server_url}"

    @property
    def parameters(self) -> List[ToolParameter]:
        """Return the parameters for the current tool name."""
        tool_name = getattr(self, '_current_tool_name', self.name)
        tool_info = self._get_tool_info(tool_name)

        if tool_info:
            input_schema = tool_info.get("inputSchema", {})
            if input_schema:
                return self._generate_parameters_from_schema(input_schema)

        # Return generic parameters by default
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
        """Generate the parameter list from inputSchema."""
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
        """Convert a JSON Schema type to ParameterType."""
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
        """Set the current tool name used to fetch tool-specific parameters."""
        self._current_tool_name = tool_name

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the MCP tool."""
        start_time = time.time()

        try:
            # Determine the tool to call: prefer the tool_name argument, else the
            # current tool name (extracted from available_tools)
            tool_name = kwargs.pop("tool_name", None) or self.name
            arguments = kwargs.pop("arguments", kwargs)  # remaining arguments become tool arguments

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
    """MCP error base class."""
    pass


class MCPToolManager:
    """MCP tool manager - simplified version."""

    def __init__(self, db=None):
        self.db = db
        self._tool_cache: Dict[str, Dict[str, Any]] = {}  # server_url -> tools_info

    async def discover_tools(
        self,
        server_url: str,
        connection_config: Dict[str, Any] = None
    ) -> tuple[bool, List[Dict[str, Any]], str | None]:
        """Discover tools on the MCP server."""
        try:
            from .client import SimpleMCPClient

            client = SimpleMCPClient(server_url, connection_config)

            async with client:
                tools = await client.list_tools()

                # Cache the tool info
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
        """Test the tool connection."""
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
