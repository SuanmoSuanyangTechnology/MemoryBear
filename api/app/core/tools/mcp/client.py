"""MCP客户端 - Model Context Protocol客户端实现"""
import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Callable
from urllib.parse import urlparse
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging_config import get_business_logger

logger = get_business_logger()


class MCPConnectionError(Exception):
    """MCP连接错误"""
    pass


class MCPProtocolError(Exception):
    """MCP协议错误"""
    pass


class MCPClient:
    """MCP客户端 - 支持HTTP和WebSocket连接"""
    
    def __init__(self, server_url: str, connection_config: Dict[str, Any] = None):
        """初始化MCP客户端
        
        Args:
            server_url: MCP服务器URL
            connection_config: 连接配置
        """
        self.server_url = server_url
        self.connection_config = connection_config or {}
        
        # 解析URL确定连接类型
        parsed_url = urlparse(server_url)
        self.connection_type = "websocket" if parsed_url.scheme in ["ws", "wss"] else "http"
        
        # 连接状态
        self._connected = False
        self._websocket = None
        self._session = None
        
        # 请求管理
        self._request_id = 0
        self._pending_requests: Dict[str, asyncio.Future] = {}
        
        # 连接池配置
        self.max_connections = self.connection_config.get("max_connections", 10)
        self.connection_timeout = self.connection_config.get("timeout", 30)
        self.retry_attempts = self.connection_config.get("retry_attempts", 3)
        self.retry_delay = self.connection_config.get("retry_delay", 1)
        
        # 健康检查
        self.health_check_interval = self.connection_config.get("health_check_interval", 60)
        self._health_check_task = None
        self._last_health_check = None
        
        # 事件回调
        self._on_connect_callbacks: List[Callable] = []
        self._on_disconnect_callbacks: List[Callable] = []
        self._on_error_callbacks: List[Callable] = []
    
    async def connect(self) -> bool:
        """连接到MCP服务器
        
        Returns:
            连接是否成功
        """
        try:
            if self._connected:
                return True
            
            logger.info(f"连接MCP服务器: {self.server_url}")
            
            if self.connection_type == "websocket":
                success = await self._connect_websocket()
            else:
                success = await self._connect_http()
            
            if success:
                self._connected = True
                await self._start_health_check()
                await self._notify_connect_callbacks()
                logger.info(f"MCP服务器连接成功: {self.server_url}")
            
            return success
            
        except Exception as e:
            logger.error(f"连接MCP服务器失败: {self.server_url}, 错误: {e}")
            await self._notify_error_callbacks(e)
            return False
    
    async def disconnect(self) -> bool:
        """断开MCP服务器连接
        
        Returns:
            断开是否成功
        """
        try:
            if not self._connected:
                return True
            
            logger.info(f"断开MCP服务器连接: {self.server_url}")
            
            # 停止健康检查
            await self._stop_health_check()
            
            # 取消所有待处理的请求
            for future in self._pending_requests.values():
                if not future.done():
                    future.cancel()
            self._pending_requests.clear()
            
            # 断开连接
            if self.connection_type == "websocket" and self._websocket:
                await self._websocket.close()
                self._websocket = None
            elif self._session:
                await self._session.close()
                self._session = None
            
            self._connected = False
            await self._notify_disconnect_callbacks()
            logger.info(f"MCP服务器连接已断开: {self.server_url}")
            
            return True
            
        except Exception as e:
            logger.error(f"断开MCP服务器连接失败: {e}")
            return False
    
    def _build_auth_headers(self) -> Dict[str, str]:
        """构建认证头"""
        headers = {}
        auth_type = self.connection_config.get("auth_type", "none")
        auth_config = self.connection_config.get("auth_config", {})
        
        if auth_type == "api_key":
            api_key = auth_config.get("api_key")
            key_name = auth_config.get("key_name", "X-API-Key")
            if api_key:
                headers[key_name] = api_key
        
        elif auth_type == "bearer_token":
            token = auth_config.get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        
        elif auth_type == "basic_auth":
            username = auth_config.get("username")
            password = auth_config.get("password")
            if username and password:
                import base64
                credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {credentials}"
        
        return headers
    
    async def _connect_websocket(self) -> bool:
        """建立WebSocket连接"""
        try:
            # WebSocket连接配置
            extra_headers = self.connection_config.get("headers", {})
            auth_headers = self._build_auth_headers()
            extra_headers.update(auth_headers)
            
            self._websocket = await websockets.connect(
                self.server_url,
                extra_headers=extra_headers,
                timeout=self.connection_timeout
            )
            
            # 启动消息监听
            asyncio.create_task(self._websocket_message_handler())
            
            # 发送初始化消息
            init_message = {
                "jsonrpc": "2.0",
                "id": self._get_next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "clientInfo": {
                        "name": "ToolManagementSystem",
                        "version": "1.0.0"
                    }
                }
            }
            
            await self._websocket.send(json.dumps(init_message))
            
            # 等待初始化响应
            response = await asyncio.wait_for(
                self._websocket.recv(),
                timeout=self.connection_timeout
            )
            
            init_response = json.loads(response)
            if "error" in init_response:
                raise MCPProtocolError(f"初始化失败: {init_response['error']}")
            
            return True
            
        except Exception as e:
            logger.error(f"WebSocket连接失败: {e}")
            return False
    
    async def _connect_http(self) -> bool:
        """建立HTTP连接"""
        try:
            # HTTP会话配置
            timeout = aiohttp.ClientTimeout(total=self.connection_timeout)
            headers = self.connection_config.get("headers", {})
            auth_headers = self._build_auth_headers()
            headers.update(auth_headers)
            
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            )
            
            # 测试连接
            test_url = f"{self.server_url}/health" if not self.server_url.endswith('/') else f"{self.server_url}health"
            
            async with self._session.get(test_url) as response:
                if response.status == 200:
                    return True
                else:
                    # 尝试根路径
                    async with self._session.get(self.server_url) as root_response:
                        return root_response.status < 400
            
        except Exception as e:
            logger.error(f"HTTP连接失败: {e}")
            if self._session:
                await self._session.close()
                self._session = None
            return False
    
    async def _websocket_message_handler(self):
        """WebSocket消息处理器"""
        try:
            while self._websocket and not self._websocket.closed:
                try:
                    message = await self._websocket.recv()
                    await self._handle_message(json.loads(message))
                except ConnectionClosed:
                    break
                except json.JSONDecodeError as e:
                    logger.error(f"解析WebSocket消息失败: {e}")
                except Exception as e:
                    logger.error(f"处理WebSocket消息失败: {e}")
                    
        except Exception as e:
            logger.error(f"WebSocket消息处理器异常: {e}")
        finally:
            self._connected = False
            await self._notify_disconnect_callbacks()
    
    async def _handle_message(self, message: Dict[str, Any]):
        """处理收到的消息"""
        try:
            # 检查是否是响应消息
            if "id" in message:
                request_id = str(message["id"])
                if request_id in self._pending_requests:
                    future = self._pending_requests.pop(request_id)
                    if not future.done():
                        future.set_result(message)
            
            # 处理通知消息
            elif "method" in message:
                await self._handle_notification(message)
                
        except Exception as e:
            logger.error(f"处理消息失败: {e}")

    @staticmethod
    async def _handle_notification(message: Dict[str, Any]):
        """处理通知消息"""
        method = message.get("method")
        params = message.get("params", {})
        
        logger.debug(f"收到MCP通知: {method}, 参数: {params}")
        
        # 这里可以根据需要处理特定的通知
        # 例如：工具列表更新、服务器状态变化等
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
        """调用MCP工具
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            timeout: 超时时间（秒）
            
        Returns:
            工具执行结果
            
        Raises:
            MCPConnectionError: 连接错误
            MCPProtocolError: 协议错误
        """
        if not self._connected:
            raise MCPConnectionError("MCP客户端未连接")
        
        request_data = {
            "jsonrpc": "2.0",
            "id": self._get_next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        
        try:
            response = await self._send_request(request_data, timeout)
            
            if "error" in response:
                error = response["error"]
                raise MCPProtocolError(f"工具调用失败: {error.get('message', '未知错误')}")
            
            return response.get("result", {})
            
        except asyncio.TimeoutError:
            raise MCPProtocolError(f"工具调用超时: {tool_name}")
    
    async def list_tools(self, timeout: int = 10) -> List[Dict[str, Any]]:
        """获取可用工具列表
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            工具列表
            
        Raises:
            MCPConnectionError: 连接错误
            MCPProtocolError: 协议错误
        """
        if not self._connected:
            raise MCPConnectionError("MCP客户端未连接")
        
        request_data = {
            "jsonrpc": "2.0",
            "id": self._get_next_request_id(),
            "method": "tools/list"
        }
        
        try:
            response = await self._send_request(request_data, timeout)
            
            if response.get("error", None) is not None:
                error = response["error"]
                raise MCPProtocolError(f"获取工具列表失败: {error.get('message', '未知错误')}")
            
            result = response.get("result", {})
            return result.get("tools", [])
            
        except asyncio.TimeoutError:
            raise MCPProtocolError("获取工具列表超时")
    
    async def _send_request(self, request_data: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送请求并等待响应
        
        Args:
            request_data: 请求数据
            timeout: 超时时间（秒）
            
        Returns:
            响应数据
        """
        if self.connection_type == "websocket":
            request_id = str(request_data["id"])
            return await self._send_websocket_request(request_data, request_id, timeout)
        else:
            return await self._send_http_request(request_data, timeout)
    
    async def _send_websocket_request(self, request_data: Dict[str, Any], request_id: str, timeout: int) -> Dict[str, Any]:
        """发送WebSocket请求"""
        if not self._websocket or self._websocket.closed:
            raise MCPConnectionError("WebSocket连接已断开")
        
        # 创建Future等待响应
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            # 发送请求
            await self._websocket.send(json.dumps(request_data))
            
            # 等待响应
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
            
        except asyncio.TimeoutError:
            await self._pending_requests.pop(request_id, None)
            raise
        except Exception as e:
            await self._pending_requests.pop(request_id, None)
            raise MCPConnectionError(f"发送WebSocket请求失败: {e}")
    
    async def _send_http_request(self, request_data: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        """发送HTTP请求"""
        if not self._session:
            raise MCPConnectionError("HTTP会话未建立")
        
        try:
            url = f"{self.server_url}/mcp" if not self.server_url.endswith('/') else f"{self.server_url}mcp"
            
            async with self._session.post(
                url,
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    async with self._session.post(
                            self.server_url,
                            json=request_data,
                            timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as root_response:
                        if root_response.status != 200:
                            error_text = await root_response.text()
                            raise MCPConnectionError(f"HTTP请求失败 {response.status}: {error_text}")

                        return await response.json()
                
        except aiohttp.ClientError as e:
            raise MCPConnectionError(f"HTTP请求失败: {e}")
    
    async def health_check(self) -> Dict[str, Any]:
        """执行健康检查
        
        Returns:
            健康状态信息
        """
        try:
            if not self._connected:
                return {
                    "healthy": False,
                    "error": "未连接",
                    "timestamp": time.time()
                }
            
            # 发送ping请求
            request_data = {
                "jsonrpc": "2.0",
                "id": self._get_next_request_id(),
                "method": "ping"
            }
            
            start_time = time.time()
            response = await self._send_request(request_data, timeout=5)
            response_time = round((time.time() - start_time) * 1000)
            
            self._last_health_check = round(time.time() * 1000)
            
            return {
                "healthy": True,
                "response_time": response_time,
                "timestamp": self._last_health_check,
                "server_info": response.get("result", {})
            }
            
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "timestamp": time.time()
            }
    
    async def _start_health_check(self):
        """启动健康检查任务"""
        if self.health_check_interval > 0:
            self._health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def _stop_health_check(self):
        """停止健康检查任务"""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None
    
    async def _health_check_loop(self):
        """健康检查循环"""
        try:
            while self._connected:
                await asyncio.sleep(self.health_check_interval)
                
                if self._connected:
                    health_status = await self.health_check()
                    if not health_status["healthy"]:
                        logger.warning(f"MCP服务器健康检查失败: {health_status.get('error')}")
                        # 可以在这里实现重连逻辑
                        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"健康检查循环异常: {e}")
    
    def _get_next_request_id(self) -> str:
        """获取下一个请求ID"""
        self._request_id += 1
        return f"req_{self._request_id}_{int(time.time() * 1000)}"
    
    # 事件回调管理
    def on_connect(self, callback: Callable):
        """注册连接回调"""
        self._on_connect_callbacks.append(callback)
    
    def on_disconnect(self, callback: Callable):
        """注册断开连接回调"""
        self._on_disconnect_callbacks.append(callback)
    
    def on_error(self, callback: Callable):
        """注册错误回调"""
        self._on_error_callbacks.append(callback)
    
    async def _notify_connect_callbacks(self):
        """通知连接回调"""
        for callback in self._on_connect_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"连接回调执行失败: {e}")
    
    async def _notify_disconnect_callbacks(self):
        """通知断开连接回调"""
        for callback in self._on_disconnect_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"断开连接回调执行失败: {e}")
    
    async def _notify_error_callbacks(self, error: Exception):
        """通知错误回调"""
        for callback in self._on_error_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error)
                else:
                    callback(error)
            except Exception as e:
                logger.error(f"错误回调执行失败: {e}")
    
    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected
    
    @property
    def last_health_check(self) -> Optional[float]:
        """获取最后一次健康检查时间"""
        return self._last_health_check
    
    def get_connection_info(self) -> Dict[str, Any]:
        """获取连接信息"""
        return {
            "server_url": self.server_url,
            "connection_type": self.connection_type,
            "connected": self._connected,
            "last_health_check": self._last_health_check,
            "pending_requests": len(self._pending_requests),
            "config": self.connection_config
        }
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.disconnect()


class MCPConnectionPool:
    """MCP连接池 - 管理多个MCP客户端连接"""
    
    def __init__(self, max_connections: int = 10):
        """初始化连接池
        
        Args:
            max_connections: 最大连接数
        """
        self.max_connections = max_connections
        self._clients: Dict[str, MCPClient] = {}
        self._lock = asyncio.Lock()
    
    async def get_client(self, server_url: str, connection_config: Dict[str, Any] = None) -> MCPClient:
        """获取或创建MCP客户端
        
        Args:
            server_url: 服务器URL
            connection_config: 连接配置
            
        Returns:
            MCP客户端实例
        """
        async with self._lock:
            if server_url in self._clients:
                client = self._clients[server_url]
                if client.is_connected:
                    return client
                else:
                    # 尝试重连
                    if await client.connect():
                        return client
                    else:
                        # 移除失效的客户端
                        del self._clients[server_url]
            
            # 检查连接数限制
            if len(self._clients) >= self.max_connections:
                # 移除最旧的连接
                oldest_url = next(iter(self._clients))
                await self._clients[oldest_url].disconnect()
                del self._clients[oldest_url]
            
            # 创建新客户端
            client = MCPClient(server_url, connection_config)
            if await client.connect():
                self._clients[server_url] = client
                return client
            else:
                raise MCPConnectionError(f"无法连接到MCP服务器: {server_url}")
    
    async def disconnect_all(self):
        """断开所有连接"""
        async with self._lock:
            for client in self._clients.values():
                await client.disconnect()
            self._clients.clear()
    
    def get_pool_status(self) -> Dict[str, Any]:
        """获取连接池状态"""
        return {
            "total_connections": len(self._clients),
            "max_connections": self.max_connections,
            "connections": {
                url: client.get_connection_info()
                for url, client in self._clients.items()
            }
        }