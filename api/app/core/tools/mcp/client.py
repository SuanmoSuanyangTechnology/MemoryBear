"""MCP客户端 - 简化版本"""
import asyncio
import json
import time
from typing import Dict, Any, List
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging_config import get_business_logger

logger = get_business_logger()


class MCPConnectionError(Exception):
    """MCP连接错误"""
    pass


class SimpleMCPClient:
    """简化的 MCP 客户端"""
    
    def __init__(self, server_url: str, connection_config: Dict[str, Any] = None):
        self.server_url = server_url
        self.connection_config = connection_config or {}
        self.timeout = self.connection_config.get("timeout", 30)
        
        # 确定连接类型
        self.is_websocket = server_url.startswith(("ws://", "wss://"))
        
        # 连接状态
        self._websocket = None
        self._session = None
        self._request_id = 0
        self._pending_requests = {}
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.disconnect()
    
    async def connect(self):
        """建立连接"""
        try:
            if self.is_websocket:
                await self._connect_websocket()
            else:
                await self._connect_http()
        except Exception as e:
            logger.error(f"MCP连接失败: {self.server_url}, 错误: {e}")
            raise MCPConnectionError(f"连接失败: {e}")
    
    async def disconnect(self):
        """断开连接"""
        try:
            if self._websocket:
                await self._websocket.close()
                self._websocket = None
            
            if self._session:
                await self._session.close()
                self._session = None
                
        except Exception as e:
            logger.error(f"断开连接失败: {e}")
    
    async def _connect_websocket(self):
        """WebSocket 连接"""
        headers = self._build_headers()
        
        self._websocket = await websockets.connect(
            self.server_url,
            extra_headers=headers,
            timeout=self.timeout
        )
        
        # 启动消息处理
        asyncio.create_task(self._handle_websocket_messages())
        
        # 发送初始化消息
        await self._send_initialize()
    
    async def _connect_http(self):
        """HTTP 连接"""
        headers = self._build_headers()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        )
        
        # 对于 ModelScope MCP 服务，需要先发送初始化请求
        if "modelscope.net" in self.server_url:
            await self._initialize_modelscope_session()
    
    async def _initialize_modelscope_session(self):
        """初始化 ModelScope MCP 会话"""
        init_request = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {
                    "name": "MemoryBear",
                    "version": "1.0.0"
                }
            }
        }
        
        try:
            async with self._session.post(
                self.server_url,
                json=init_request
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise MCPConnectionError(f"初始化失败 {response.status}: {error_text}")
                
                init_response = await response.json()
                if "error" in init_response:
                    raise MCPConnectionError(f"初始化失败: {init_response['error']}")
                
                # 获取 session ID
                session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
                if session_id:
                    self._session.headers.update({"Mcp-Session-Id": session_id})
                    
                    # 发送 initialized 通知
                    initialized_notification = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized"
                    }
                    
                    async with self._session.post(
                        self.server_url,
                        json=initialized_notification
                    ) as notif_response:
                        pass
                    
        except aiohttp.ClientError as e:
            raise MCPConnectionError(f"初始化连接失败: {e}")
    
    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        
        # 添加认证头
        auth_config = self.connection_config.get("auth_config", {})
        auth_type = self.connection_config.get("auth_type", "none")
        
        if auth_type == "bearer_token":
            token = auth_config.get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api_key":
            key = auth_config.get("api_key")
            header_name = auth_config.get("key_name", "X-API-Key")
            if key:
                headers[header_name] = key
        elif auth_type == "basic_auth":
            username = auth_config.get("username")
            password = auth_config.get("password")
            if username and password:
                import base64
                credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {credentials}"
        
        return headers
    
    async def _send_initialize(self):
        """发送初始化消息"""
        init_message = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {
                    "name": "MemoryBear",
                    "version": "1.0.0"
                }
            }
        }
        
        await self._websocket.send(json.dumps(init_message))
        
        # 等待初始化响应
        response = await asyncio.wait_for(
            self._websocket.recv(),
            timeout=self.timeout
        )
        
        init_response = json.loads(response)
        if "error" in init_response:
            raise MCPConnectionError(f"初始化失败: {init_response['error']}")
    
    async def _handle_websocket_messages(self):
        """处理 WebSocket 消息"""
        try:
            while self._websocket and not self._websocket.closed:
                try:
                    message = await self._websocket.recv()
                    data = json.loads(message)
                    
                    # 处理响应
                    if "id" in data:
                        request_id = str(data["id"])
                        if request_id in self._pending_requests:
                            future = self._pending_requests.pop(request_id)
                            if not future.done():
                                future.set_result(data)
                    
                except ConnectionClosed:
                    break
                except Exception as e:
                    logger.error(f"处理WebSocket消息失败: {e}")
                    
        except Exception as e:
            logger.error(f"WebSocket消息处理异常: {e}")
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用工具"""
        request_data = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        
        if self.is_websocket:
            response = await self._send_websocket_request(request_data)
        else:
            response = await self._send_http_request(request_data)
        
        if "error" in response:
            error = response["error"]
            raise MCPConnectionError(f"工具调用失败: {error.get('message', '未知错误')}")
        
        return response.get("result", {})
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取工具列表"""
        request_data = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "tools/list",
            "params": {}
        }
        
        if self.is_websocket:
            response = await self._send_websocket_request(request_data)
        else:
            response = await self._send_http_request(request_data)
        
        if "error" in response:
            error = response["error"]
            raise MCPConnectionError(f"获取工具列表失败: {error.get('message', '未知错误')}")
        
        result = response.get("result", {})
        return result.get("tools", [])
    
    async def _send_websocket_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """发送WebSocket请求"""
        request_id = str(request_data["id"])
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            await self._websocket.send(json.dumps(request_data))
            response = await asyncio.wait_for(future, timeout=self.timeout)
            return response
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise
    
    async def _send_http_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """发送HTTP请求"""
        try:
            async with self._session.post(
                self.server_url,
                json=request_data
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise MCPConnectionError(f"HTTP请求失败 {response.status}: {error_text}")
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            raise MCPConnectionError(f"HTTP请求失败: {e}")
    
    def _get_request_id(self) -> str:
        """获取请求ID"""
        self._request_id += 1
        return f"req_{self._request_id}_{int(time.time() * 1000)}"