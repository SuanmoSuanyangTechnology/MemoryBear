"""MCP工具基类"""
import time
from typing import Dict, Any, List
import aiohttp

from app.models.tool_model import ToolType
from app.core.tools.base import BaseTool
from app.schemas.tool_schema import ToolParameter, ToolResult, ParameterType
from app.core.logging_config import get_business_logger

logger = get_business_logger()


class MCPTool(BaseTool):
    """MCP工具 - Model Context Protocol工具"""
    
    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """初始化MCP工具
        
        Args:
            tool_id: 工具ID
            config: 工具配置
        """
        super().__init__(tool_id, config)
        self.server_url = config.get("server_url", "")
        self.connection_config = config.get("connection_config", {})
        self.available_tools = config.get("available_tools", [])
        self._client = None
        self._connected = False
    
    @property
    def name(self) -> str:
        """工具名称"""
        return f"mcp_tool_{self.tool_id[:8]}"
    
    @property
    def description(self) -> str:
        """工具描述"""
        return f"MCP工具 - 连接到 {self.server_url}"
    
    @property
    def tool_type(self) -> ToolType:
        """工具类型"""
        return ToolType.MCP
    
    @property
    def parameters(self) -> List[ToolParameter]:
        """工具参数定义"""
        params = []
        
        # 添加工具选择参数
        if len(self.available_tools) > 1:
            params.append(ToolParameter(
                name="tool_name",
                type=ParameterType.STRING,
                description="要调用的MCP工具名称",
                required=True,
                enum=self.available_tools
            ))
        
        # 添加通用参数
        params.extend([
            ToolParameter(
                name="arguments",
                type=ParameterType.OBJECT,
                description="工具参数（JSON对象）",
                required=False,
                default={}
            ),
            ToolParameter(
                name="timeout",
                type=ParameterType.INTEGER,
                description="超时时间（秒）",
                required=False,
                default=30,
                minimum=1,
                maximum=300
            )
        ])
        
        return params
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行MCP工具"""
        start_time = time.time()
        
        try:
            # 确保连接
            if not self._connected:
                await self.connect()
            
            # 确定要调用的工具
            tool_name = kwargs.get("tool_name")
            if not tool_name and len(self.available_tools) == 1:
                tool_name = self.available_tools[0]
            
            if not tool_name:
                raise ValueError("必须指定要调用的MCP工具名称")
            
            if tool_name not in self.available_tools:
                raise ValueError(f"MCP工具不存在: {tool_name}")
            
            # 获取参数
            arguments = kwargs.get("arguments", {})
            timeout = kwargs.get("timeout", 30)
            
            # 调用MCP工具
            result = await self._call_mcp_tool(tool_name, arguments, timeout)
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="MCP_ERROR",
                execution_time=execution_time
            )
    
    async def connect(self) -> bool:
        """连接到MCP服务器"""
        try:
            from .client import MCPClient
            
            if self._connected:
                return True
            
            self._client = MCPClient(self.server_url, self.connection_config)
            
            if await self._client.connect():
                self._connected = True
                # 更新可用工具列表
                await self._update_available_tools()
                logger.info(f"MCP服务器连接成功: {self.server_url}")
                return True
            else:
                logger.error(f"MCP服务器连接失败: {self.server_url}")
                return False
            
        except Exception as e:
            logger.error(f"MCP服务器连接异常: {self.server_url}, 错误: {e}")
            self._connected = False
            return False
    
    async def _update_available_tools(self):
        """更新可用工具列表"""
        try:
            if self._client and self._connected:
                tools = await self._client.list_tools()
                self.available_tools = [tool.get("name") for tool in tools if tool.get("name")]
                logger.info(f"MCP工具列表已更新: {len(self.available_tools)} 个工具")
        except Exception as e:
            logger.error(f"更新MCP工具列表失败: {e}")
    
    async def disconnect(self) -> bool:
        """断开MCP服务器连接"""
        try:
            if self._client:
                await self._client.disconnect()
                self._client = None
            
            self._connected = False
            logger.info(f"MCP服务器连接已断开: {self.server_url}")
            return True
            
        except Exception as e:
            logger.error(f"断开MCP服务器连接失败: {e}")
            return False
    
    def get_health_status(self) -> Dict[str, Any]:
        """获取MCP服务健康状态"""
        return {
            "connected": self._connected,
            "server_url": self.server_url,
            "available_tools": self.available_tools,
            "last_check": time.time()
        }
    
    async def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: int) -> Any:
        """调用MCP工具"""
        if not self._client or not self._connected:
            raise Exception("MCP客户端未连接")
        
        try:
            result = await self._client.call_tool(tool_name, arguments, timeout)
            return result
        except Exception as e:
            logger.error(f"MCP工具调用失败: {tool_name}, 错误: {e}")
            raise
    
    async def list_available_tools(self) -> List[Dict[str, Any]]:
        """列出可用的MCP工具"""
        try:
            if not self._connected:
                await self.connect()
            
            if self._client:
                tools = await self._client.list_tools()
                self.available_tools = [tool.get("name") for tool in tools if tool.get("name")]
                return tools
            
            return []
            
        except Exception as e:
            logger.error(f"获取MCP工具列表失败: {e}")
            return []
    
    def test_connection(self) -> Dict[str, Any]:
        """测试MCP连接"""
        try:
            # 这里应该实现同步的连接测试
            # 为了简化，返回基本信息
            return {
                "success": bool(self.server_url),
                "server_url": self.server_url,
                "connected": self._connected,
                "available_tools_count": len(self.available_tools),
                "message": "MCP配置有效" if self.server_url else "缺少服务器URL配置"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }