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
        self.is_sse = "/sse" in server_url.lower()
        
        # 连接状态
        self._websocket = None
        self._session = None
        self._request_id = 0
        self._pending_requests = {}
        self._server_capabilities = {}
        self._endpoint_url = None  # SSE endpoint URL
        self._sse_task = None
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
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
            if self._sse_task:
                self._sse_task.cancel()
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
        asyncio.create_task(self._handle_websocket_messages())
        await self._send_initialize()
    
    async def _connect_http(self):
        """HTTP 连接"""
        headers = self._build_headers()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        
        if self.is_sse:
            await self._initialize_sse_session()
        elif "modelscope.net" in self.server_url:
            await self._initialize_modelscope_session()
    
    async def _initialize_sse_session(self):
        """初始化 SSE MCP 会话 - 参考 Dify 实现"""
        try:
            # 建立 SSE 连接
            response = await self._session.get(self.server_url)
            
            if response.status != 200:
                error_text = await response.text()
                raise MCPConnectionError(f"SSE 连接失败 {response.status}: {error_text}")
            
            # 启动 SSE 读取任务
            self._sse_task = asyncio.create_task(self._read_sse_stream(response))
            
            # 等待获取 endpoint URL
            for _ in range(10):
                if self._endpoint_url:
                    break
                await asyncio.sleep(1)
            
            if not self._endpoint_url:
                raise MCPConnectionError("未能获取 endpoint URL")
            
            # 发送 initialize 请求到 endpoint
            init_request = {
                "jsonrpc": "2.0",
                "id": self._get_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "MemoryBear", "version": "1.0.0"}
                }
            }
            
            init_response = await self._send_sse_request(init_request)
            if "error" in init_response:
                raise MCPConnectionError(f"初始化失败: {init_response['error']}")
            
            result = init_response.get("result", {})
            self._server_capabilities = result.get("capabilities", {})
            
            # 发送 initialized 通知
            await self._send_sse_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})
                    
        except aiohttp.ClientError as e:
            raise MCPConnectionError(f"初始化连接失败: {e}")
    
    async def _read_sse_stream(self, response):
        """读取 SSE 流"""
        try:
            async for line in response.content:
                line = line.decode('utf-8').strip()
                
                if line.startswith('event:'):
                    continue
                
                if line.startswith('data:'):
                    data = line[5:].strip()  # 去除 'data:' 后的空格
                    if not data or data == '[DONE]':
                        continue
                    
                    try:
                        # 处理 endpoint 事件（相对路径或绝对路径）
                        if not self._endpoint_url:
                            # 如果是相对路径，拼接成完整 URL
                            if data.startswith('/'):
                                from urllib.parse import urlparse, urlunparse
                                parsed = urlparse(self.server_url)
                                self._endpoint_url = f"{parsed.scheme}://{parsed.netloc}{data}"
                            else:
                                self._endpoint_url = data
                            logger.info(f"获取到 endpoint URL: {self._endpoint_url}")
                            continue
                        
                        # 处理 message 事件
                        message = json.loads(data)
                        request_id = message.get("id")
                        if request_id and request_id in self._pending_requests:
                            future = self._pending_requests.pop(request_id)
                            if not future.done():
                                future.set_result(message)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"SSE 流读取错误: {e}")
    
    async def _send_sse_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """通过 SSE endpoint 发送请求"""
        if not self._endpoint_url:
            raise MCPConnectionError("endpoint URL 未初始化")
        
        request_id = request["id"]
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            async with self._session.post(self._endpoint_url, json=request) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise MCPConnectionError(f"请求失败 {response.status}: {error_text}")
            
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise MCPConnectionError("请求超时")
    
    async def _send_sse_notification(self, notification: Dict[str, Any]):
        """发送通知（无需响应）"""
        if not self._endpoint_url:
            raise MCPConnectionError("endpoint URL 未初始化")
        
        async with self._session.post(self._endpoint_url, json=notification) as response:
            if response.status != 200:
                logger.warning(f"通知发送失败: {response.status}")
    
    async def _initialize_modelscope_session(self):
        """初始化 ModelScope MCP 会话"""
        init_request = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "MemoryBear", "version": "1.0.0"}
            }
        }
        
        try:
            async with self._session.post(self.server_url, json=init_request) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise MCPConnectionError(f"初始化失败 {response.status}: {error_text}")
                
                init_response = await response.json()
                if "error" in init_response:
                    raise MCPConnectionError(f"初始化失败: {init_response['error']}")
                
                session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
                if session_id:
                    self._session.headers.update({"Mcp-Session-Id": session_id})
                    
                    initialized_notification = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized"
                    }
                    
                    async with self._session.post(self.server_url, json=initialized_notification):
                        pass
                    
        except aiohttp.ClientError as e:
            raise MCPConnectionError(f"初始化连接失败: {e}")
    
    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        # 基础 headers
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        
        # 合并 connection_config 中的自定义 headers
        custom_headers = self.connection_config.get("headers", {})
        if custom_headers:
            headers.update(custom_headers)
        
        # 处理认证配置（认证 headers 优先级更高）
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
        """发送初始化消息（WebSocket）"""
        init_message = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "MemoryBear", "version": "1.0.0"}
            }
        }
        
        await self._websocket.send(json.dumps(init_message))
        response = await self._websocket.recv()
        response_data = json.loads(response)
        
        if "error" in response_data:
            raise MCPConnectionError(f"初始化失败: {response_data['error']}")
        
        result = response_data.get("result", {})
        self._server_capabilities = result.get("capabilities", {})
        
        await self._websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }))
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取工具列表"""
        request = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "tools/list"
        }
        
        if self.is_websocket:
            await self._websocket.send(json.dumps(request))
            response = await self._websocket.recv()
            response_data = json.loads(response)
        elif self.is_sse:
            response_data = await self._send_sse_request(request)
        else:
            async with self._session.post(self.server_url, json=request) as response:
                response_data = await response.json()
        
        if "error" in response_data:
            raise MCPConnectionError(f"获取工具列表失败: {response_data['error']}")
        
        result = response_data.get("result", {})
        return result.get("tools", [])
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用工具"""
        request = {
            "jsonrpc": "2.0",
            "id": self._get_request_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        }
        
        if self.is_websocket:
            await self._websocket.send(json.dumps(request))
            response = await self._websocket.recv()
            response_data = json.loads(response)
        elif self.is_sse:
            response_data = await self._send_sse_request(request)
        else:
            async with self._session.post(self.server_url, json=request) as response:
                response_data = await response.json()
        
        if "error" in response_data:
            error = response_data["error"]
            raise MCPConnectionError(f"工具调用失败: {error.get('message', '未知错误')}")
        
        return response_data.get("result", {})
    
    def _get_request_id(self) -> int:
        """生成请求 ID"""
        self._request_id += 1
        return self._request_id
    
    async def _handle_websocket_messages(self):
        """处理 WebSocket 消息"""
        try:
            async for message in self._websocket:
                data = json.loads(message)
                request_id = data.get("id")
                if request_id and request_id in self._pending_requests:
                    future = self._pending_requests.pop(request_id)
                    if not future.done():
                        future.set_result(data)
        except ConnectionClosed:
            logger.info("WebSocket 连接已关闭")
        except Exception as e:
            logger.error(f"WebSocket 消息处理错误: {e}")
