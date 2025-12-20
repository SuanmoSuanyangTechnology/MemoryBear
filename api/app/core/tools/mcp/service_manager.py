"""MCP服务管理器 - 管理MCP服务的注册、更新、删除和状态监控"""
import asyncio
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.tool_model import MCPToolConfig, ToolConfig, ToolType
from app.core.logging_config import get_business_logger
from .client import MCPClient, MCPConnectionPool

logger = get_business_logger()


class MCPServiceManager:
    """MCP服务管理器 - 管理MCP服务的生命周期"""
    
    def __init__(self, db: Session):
        """初始化MCP服务管理器
        
        Args:
            db: 数据库会话
        """
        self.db = db
        self.connection_pool = MCPConnectionPool(max_connections=20)
        
        # 服务状态管理
        self._services: Dict[str, Dict[str, Any]] = {}  # service_id -> service_info
        self._monitoring_tasks: Dict[str, asyncio.Task] = {}  # service_id -> monitoring_task
        
        # 配置
        self.health_check_interval = 60  # 健康检查间隔（秒）
        self.max_retry_attempts = 3  # 最大重试次数
        self.retry_delay = 5  # 重试延迟（秒）
        
        # 状态
        self._running = False
        self._manager_task = None
    
    async def start(self):
        """启动服务管理器"""
        if self._running:
            return
        
        self._running = True
        logger.info("MCP服务管理器启动")
        
        # 加载现有服务
        await self._load_existing_services()
        
        # 启动管理任务
        self._manager_task = asyncio.create_task(self._management_loop())
    
    async def stop(self):
        """停止服务管理器"""
        if not self._running:
            return
        
        self._running = False
        logger.info("MCP服务管理器停止")
        
        # 停止管理任务
        if self._manager_task:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except asyncio.CancelledError:
                pass
        
        # 停止所有监控任务
        for task in self._monitoring_tasks.values():
            task.cancel()
        
        if self._monitoring_tasks:
            await asyncio.gather(*self._monitoring_tasks.values(), return_exceptions=True)
        
        self._monitoring_tasks.clear()
        
        # 断开所有连接
        await self.connection_pool.disconnect_all()
    
    async def register_service(
        self,
        server_url: str,
        connection_config: Dict[str, Any],
        tenant_id: uuid.UUID,
        service_name: str = None
    ) -> Tuple[bool, str, Optional[str]]:
        """注册MCP服务
        
        Args:
            server_url: 服务器URL
            connection_config: 连接配置
            tenant_id: 租户ID
            service_name: 服务名称（可选）
            
        Returns:
            (是否成功, 服务ID或错误信息, 错误详情)
        """
        try:
            # 检查服务是否已存在
            existing_service = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.server_url == server_url
            ).first()
            
            if existing_service:
                return False, "服务已存在", f"URL {server_url} 已被注册"
            
            # 测试连接
            try:
                client = MCPClient(server_url, connection_config)
                if not await client.connect():
                    return False, "连接测试失败", "无法连接到MCP服务器"
                
                # 获取可用工具
                available_tools = await client.list_tools()
                tool_names = [tool.get("name") for tool in available_tools if tool.get("name")]
                
                await client.disconnect()
                
            except Exception as e:
                return False, "连接测试失败", str(e)
            
            # 创建工具配置
            if not service_name:
                service_name = f"mcp_service_{server_url.split('/')[-1]}"
            
            tool_config = ToolConfig(
                name=service_name,
                description=f"MCP服务 - {server_url}",
                tool_type=ToolType.MCP.value,
                tenant_id=tenant_id,
                version="1.0.0",
                config_data={
                    "server_url": server_url,
                    "connection_config": connection_config
                }
            )
            
            self.db.add(tool_config)
            self.db.flush()
            
            # 创建MCP特定配置
            mcp_config = MCPToolConfig(
                id=tool_config.id,
                server_url=server_url,
                connection_config=connection_config,
                available_tools=tool_names,
                health_status="healthy",
                last_health_check=datetime.utcnow()
            )
            
            self.db.add(mcp_config)
            self.db.commit()
            
            service_id = str(tool_config.id)
            
            # 添加到内存管理
            self._services[service_id] = {
                "id": service_id,
                "server_url": server_url,
                "connection_config": connection_config,
                "tenant_id": tenant_id,
                "available_tools": tool_names,
                "status": "healthy",
                "last_health_check": time.time(),
                "retry_count": 0,
                "created_at": time.time()
            }
            
            # 启动监控
            await self._start_service_monitoring(service_id)
            
            logger.info(f"MCP服务注册成功: {service_id} ({server_url})")
            return True, service_id, None
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"注册MCP服务失败: {server_url}, 错误: {e}")
            return False, "注册失败", str(e)
    
    async def unregister_service(self, service_id: str) -> Tuple[bool, str]:
        """注销MCP服务
        
        Args:
            service_id: 服务ID
            
        Returns:
            (是否成功, 错误信息)
        """
        try:
            # 从数据库删除
            tool_config = self.db.get(ToolConfig, uuid.UUID(service_id))
            if not tool_config:
                return False, "服务不存在"
            
            self.db.delete(tool_config)
            self.db.commit()
            
            # 停止监控
            await self._stop_service_monitoring(service_id)
            
            # 从内存移除
            if service_id in self._services:
                del self._services[service_id]
            
            logger.info(f"MCP服务注销成功: {service_id}")
            return True, ""
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"注销MCP服务失败: {service_id}, 错误: {e}")
            return False, str(e)
    
    async def update_service(
        self,
        service_id: str,
        connection_config: Dict[str, Any] = None,
        enabled: bool = None
    ) -> Tuple[bool, str]:
        """更新MCP服务配置
        
        Args:
            service_id: 服务ID
            connection_config: 新的连接配置
            enabled: 是否启用
            
        Returns:
            (是否成功, 错误信息)
        """
        try:
            # 更新数据库
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == uuid.UUID(service_id)
            ).first()
            
            if not mcp_config:
                return False, "服务不存在"
            
            tool_config = mcp_config.base_config
            
            if connection_config is not None:
                mcp_config.connection_config = connection_config
                tool_config.config_data["connection_config"] = connection_config
            
            if enabled is not None:
                tool_config.is_enabled = enabled
            
            self.db.commit()
            
            # 更新内存状态
            if service_id in self._services:
                if connection_config is not None:
                    self._services[service_id]["connection_config"] = connection_config
                
                # 如果配置有变化，重启监控
                if connection_config is not None:
                    await self._restart_service_monitoring(service_id)
            
            logger.info(f"MCP服务更新成功: {service_id}")
            return True, ""
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"更新MCP服务失败: {service_id}, 错误: {e}")
            return False, str(e)
    
    async def get_service_status(self, service_id: str) -> Optional[Dict[str, Any]]:
        """获取服务状态
        
        Args:
            service_id: 服务ID
            
        Returns:
            服务状态信息
        """
        if service_id not in self._services:
            return None
        
        service_info = self._services[service_id].copy()
        
        # 添加实时健康检查
        try:
            client = await self.connection_pool.get_client(
                service_info["server_url"],
                service_info["connection_config"]
            )
            
            health_status = await client.health_check()
            service_info["real_time_health"] = health_status
            
        except Exception as e:
            service_info["real_time_health"] = {
                "healthy": False,
                "error": str(e),
                "timestamp": time.time()
            }
        
        return service_info
    
    async def list_services(self, tenant_id: uuid.UUID = None) -> List[Dict[str, Any]]:
        """列出所有服务
        
        Args:
            tenant_id: 租户ID过滤
            
        Returns:
            服务列表
        """
        services = []
        
        for service_id, service_info in self._services.items():
            if tenant_id and service_info["tenant_id"] != tenant_id:
                continue
            
            services.append(service_info.copy())
        
        return services
    
    async def get_service_tools(self, service_id: str) -> List[Dict[str, Any]]:
        """获取服务的可用工具
        
        Args:
            service_id: 服务ID
            
        Returns:
            工具列表
        """
        if service_id not in self._services:
            return []
        
        service_info = self._services[service_id]
        
        try:
            client = await self.connection_pool.get_client(
                service_info["server_url"],
                service_info["connection_config"]
            )
            
            tools = await client.list_tools()
            
            # 更新缓存的工具列表
            tool_names = [tool.get("name") for tool in tools if tool.get("name")]
            service_info["available_tools"] = tool_names
            
            # 更新数据库
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == uuid.UUID(service_id)
            ).first()
            
            if mcp_config:
                mcp_config.available_tools = tool_names
                self.db.commit()
            
            return tools
            
        except Exception as e:
            logger.error(f"获取服务工具失败: {service_id}, 错误: {e}")
            return []
    
    async def call_service_tool(
        self,
        service_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: int = 30
    ) -> Dict[str, Any]:
        """调用服务工具
        
        Args:
            service_id: 服务ID
            tool_name: 工具名称
            arguments: 工具参数
            timeout: 超时时间
            
        Returns:
            执行结果
        """
        if service_id not in self._services:
            raise ValueError(f"服务不存在: {service_id}")
        
        service_info = self._services[service_id]
        
        try:
            client = await self.connection_pool.get_client(
                service_info["server_url"],
                service_info["connection_config"]
            )
            
            result = await client.call_tool(tool_name, arguments, timeout)
            
            # 更新服务状态为健康
            service_info["status"] = "healthy"
            service_info["last_health_check"] = time.time()
            service_info["retry_count"] = 0
            
            return result
            
        except Exception as e:
            # 更新服务状态为错误
            service_info["status"] = "error"
            service_info["last_error"] = str(e)
            service_info["retry_count"] += 1
            
            logger.error(f"调用服务工具失败: {service_id}/{tool_name}, 错误: {e}")
            raise
    
    async def _load_existing_services(self):
        """加载现有服务"""
        try:
            mcp_configs = self.db.query(MCPToolConfig).join(ToolConfig).filter(
                ToolConfig.is_enabled == True
            ).all()
            
            for mcp_config in mcp_configs:
                tool_config = mcp_config.base_config
                service_id = str(mcp_config.id)
                
                self._services[service_id] = {
                    "id": service_id,
                    "server_url": mcp_config.server_url,
                    "connection_config": mcp_config.connection_config or {},
                    "tenant_id": tool_config.tenant_id,
                    "available_tools": mcp_config.available_tools or [],
                    "status": mcp_config.health_status or "unknown",
                    "last_health_check": mcp_config.last_health_check.timestamp() if mcp_config.last_health_check else 0,
                    "retry_count": 0,
                    "created_at": tool_config.created_at.timestamp()
                }
                
                # 启动监控
                await self._start_service_monitoring(service_id)
            
            logger.info(f"加载了 {len(mcp_configs)} 个MCP服务")
            
        except Exception as e:
            logger.error(f"加载现有服务失败: {e}")
    
    async def _start_service_monitoring(self, service_id: str):
        """启动服务监控"""
        if service_id in self._monitoring_tasks:
            return
        
        task = asyncio.create_task(self._monitor_service(service_id))
        self._monitoring_tasks[service_id] = task
    
    async def _stop_service_monitoring(self, service_id: str):
        """停止服务监控"""
        if service_id in self._monitoring_tasks:
            task = self._monitoring_tasks.pop(service_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    async def _restart_service_monitoring(self, service_id: str):
        """重启服务监控"""
        await self._stop_service_monitoring(service_id)
        await self._start_service_monitoring(service_id)
    
    async def _monitor_service(self, service_id: str):
        """监控单个服务"""
        try:
            while self._running and service_id in self._services:
                service_info = self._services[service_id]
                
                try:
                    # 执行健康检查
                    client = await self.connection_pool.get_client(
                        service_info["server_url"],
                        service_info["connection_config"]
                    )
                    
                    health_status = await client.health_check()
                    
                    if health_status["healthy"]:
                        # 服务健康
                        service_info["status"] = "healthy"
                        service_info["retry_count"] = 0
                        
                        # 更新工具列表
                        try:
                            tools = await client.list_tools()
                            tool_names = [tool.get("name") for tool in tools if tool.get("name")]
                            service_info["available_tools"] = tool_names
                        except Exception as e:
                            logger.warning(f"更新工具列表失败: {service_id}, 错误: {e}")
                    
                    else:
                        # 服务不健康
                        service_info["status"] = "unhealthy"
                        service_info["last_error"] = health_status.get("error", "健康检查失败")
                        service_info["retry_count"] += 1
                    
                    service_info["last_health_check"] = time.time()
                    
                    # 更新数据库
                    await self._update_service_health_in_db(service_id, health_status)
                    
                except Exception as e:
                    # 监控异常
                    service_info["status"] = "error"
                    service_info["last_error"] = str(e)
                    service_info["retry_count"] += 1
                    service_info["last_health_check"] = time.time()
                    
                    logger.error(f"服务监控异常: {service_id}, 错误: {e}")
                    
                    # 如果重试次数过多，暂停监控
                    if service_info["retry_count"] >= self.max_retry_attempts:
                        logger.warning(f"服务 {service_id} 重试次数过多，暂停监控")
                        await asyncio.sleep(self.health_check_interval * 5)  # 延长等待时间
                        service_info["retry_count"] = 0  # 重置重试计数
                
                # 等待下次检查
                await asyncio.sleep(self.health_check_interval)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"服务监控任务异常: {service_id}, 错误: {e}")
    
    async def _update_service_health_in_db(self, service_id: str, health_status: Dict[str, Any]):
        """更新数据库中的服务健康状态"""
        try:
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == uuid.UUID(service_id)
            ).first()
            
            if mcp_config:
                mcp_config.health_status = "healthy" if health_status["healthy"] else "unhealthy"
                mcp_config.last_health_check = datetime.utcnow()
                
                if not health_status["healthy"]:
                    mcp_config.error_message = health_status.get("error", "")
                else:
                    mcp_config.error_message = None
                
                self.db.commit()
                
        except Exception as e:
            logger.error(f"更新数据库健康状态失败: {service_id}, 错误: {e}")
            self.db.rollback()
    
    async def _management_loop(self):
        """管理循环 - 处理服务清理等任务"""
        try:
            while self._running:
                # 清理失效的服务
                await self._cleanup_failed_services()
                
                # 等待下次循环
                await asyncio.sleep(300)  # 5分钟
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"管理循环异常: {e}")
    
    async def _cleanup_failed_services(self):
        """清理长期失效的服务"""
        try:
            current_time = time.time()
            cleanup_threshold = 24 * 60 * 60  # 24小时
            
            services_to_cleanup = []
            
            for service_id, service_info in self._services.items():
                # 检查服务是否长期失效
                if (service_info["status"] in ["error", "unhealthy"] and
                    current_time - service_info["last_health_check"] > cleanup_threshold):
                    
                    services_to_cleanup.append(service_id)
            
            for service_id in services_to_cleanup:
                logger.warning(f"清理长期失效的服务: {service_id}")
                
                # 停止监控但不删除数据库记录
                await self._stop_service_monitoring(service_id)
                
                # 标记为禁用
                tool_config = self.db.get(ToolConfig, uuid.UUID(service_id))
                if tool_config:
                    tool_config.is_enabled = False
                    self.db.commit()
                
                # 从内存移除
                del self._services[service_id]
            
        except Exception as e:
            logger.error(f"清理失效服务失败: {e}")
    
    def get_manager_status(self) -> Dict[str, Any]:
        """获取管理器状态"""
        return {
            "running": self._running,
            "total_services": len(self._services),
            "healthy_services": len([s for s in self._services.values() if s["status"] == "healthy"]),
            "unhealthy_services": len([s for s in self._services.values() if s["status"] in ["unhealthy", "error"]]),
            "monitoring_tasks": len(self._monitoring_tasks),
            "connection_pool_status": self.connection_pool.get_pool_status()
        }