"""工具注册表 - 管理所有工具的元数据和状态"""
import uuid
import asyncio
from typing import Dict, List, Optional, Type, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.tool_model import (
    ToolConfig, BuiltinToolConfig, CustomToolConfig, MCPToolConfig,
    ToolType, ToolStatus, ToolExecution, ExecutionStatus
)
from app.core.logging_config import get_business_logger
from .base import BaseTool, ToolInfo
from .custom.base import CustomTool
from .mcp.base import MCPTool

logger = get_business_logger()


class ToolRegistry:
    """工具注册表 - 管理所有工具的元数据和实例"""
    
    def __init__(self, db: Session):
        """初始化工具注册表
        
        Args:
            db: 数据库会话
        """
        self.db = db
        self._tools: Dict[str, BaseTool] = {}  # 工具实例缓存
        self._tool_classes: Dict[str, Type[BaseTool]] = {}  # 工具类注册表
        self._lock = asyncio.Lock()  # 异步锁
    
    def register_tool_class(self, tool_class: Type[BaseTool], class_name: str = None):
        """注册工具类
        
        Args:
            tool_class: 工具类
            class_name: 类名（可选，默认使用类的__name__）
        """
        class_name = class_name or tool_class.__name__
        self._tool_classes[class_name] = tool_class
        logger.info(f"工具类已注册: {class_name}")
    
    async def register_tool(self, tool: BaseTool, tenant_id: Optional[uuid.UUID] = None) -> bool:
        """注册工具实例到系统
        
        Args:
            tool: 工具实例
            tenant_id: 租户ID（内置工具可以为None，表示全局工具）
            
        Returns:
            注册是否成功
        """
        async with self._lock:
            try:
                # 检查工具是否已存在
                if tenant_id:
                    existing_config = self.db.query(ToolConfig).filter(
                        and_(
                            ToolConfig.name == tool.name,
                            ToolConfig.tenant_id == tenant_id,
                            ToolConfig.tool_type == tool.tool_type.value
                        )
                    ).first()
                else:
                    # 全局工具（内置工具）
                    existing_config = self.db.query(ToolConfig).filter(
                        and_(
                            ToolConfig.name == tool.name,
                            ToolConfig.tenant_id.is_(None),
                            ToolConfig.tool_type == tool.tool_type.value
                        )
                    ).first()
                
                if existing_config:
                    logger.warning(f"工具已存在: {tool.name} (tenant: {tenant_id or 'global'})")
                    return False
                
                # 创建工具配置
                tool_config = ToolConfig(
                    name=tool.name,
                    description=tool.description,
                    tool_type=tool.tool_type.value,
                    tenant_id=tenant_id,
                    version=tool.version,
                    tags=tool.tags,
                    config_data=tool.config
                )
                
                self.db.add(tool_config)
                self.db.flush()  # 获取ID
                
                # 根据工具类型创建特定配置
                if tool.tool_type == ToolType.BUILTIN:
                    builtin_config = BuiltinToolConfig(
                        id=tool_config.id,
                        tool_class=tool.__class__.__name__,
                        parameters=tool.config.get("parameters", {})
                    )
                    self.db.add(builtin_config)
                
                elif tool.tool_type == ToolType.CUSTOM:
                    custom_config = CustomToolConfig(
                        id=tool_config.id,
                        schema_url=tool.config.get("schema_url"),
                        schema_content=tool.config.get("schema_content"),
                        auth_type=tool.config.get("auth_type", "none"),
                        auth_config=tool.config.get("auth_config", {}),
                        base_url=tool.config.get("base_url"),
                        timeout=tool.config.get("timeout", 30)
                    )
                    self.db.add(custom_config)
                
                elif tool.tool_type == ToolType.MCP:
                    mcp_config = MCPToolConfig(
                        id=tool_config.id,
                        server_url=tool.config.get("server_url"),
                        connection_config=tool.config.get("connection_config", {}),
                        available_tools=tool.config.get("available_tools", [])
                    )
                    self.db.add(mcp_config)
                
                self.db.commit()
                
                # 缓存工具实例
                tool.tool_id = str(tool_config.id)
                self._tools[str(tool_config.id)] = tool
                
                logger.info(f"工具注册成功: {tool.name} (ID: {tool_config.id})")
                return True
                
            except Exception as e:
                self.db.rollback()
                logger.error(f"工具注册失败: {tool.name}, 错误: {e}")
                return False
    
    async def unregister_tool(self, tool_id: str) -> bool:
        """从系统注销工具
        
        Args:
            tool_id: 工具ID
            
        Returns:
            注销是否成功
        """
        async with self._lock:
            try:
                # 检查工具是否存在
                tool_config = self.db.get(ToolConfig, uuid.UUID(tool_id))
                if not tool_config:
                    logger.warning(f"工具不存在: {tool_id}")
                    return False
                
                # 检查是否有正在执行的任务
                running_executions = self.db.query(ToolExecution).filter(
                    and_(
                        ToolExecution.tool_config_id == uuid.UUID(tool_id),
                        ToolExecution.status.in_([ExecutionStatus.PENDING.value, ExecutionStatus.RUNNING.value])
                    )
                ).count()
                
                if running_executions > 0:
                    logger.warning(f"工具有正在执行的任务，无法注销: {tool_id}")
                    return False
                
                # 删除工具配置（级联删除相关记录）
                self.db.delete(tool_config)
                self.db.commit()
                
                # 从缓存中移除
                if tool_id in self._tools:
                    del self._tools[tool_id]
                
                logger.info(f"工具注销成功: {tool_id}")
                return True
                
            except Exception as e:
                self.db.rollback()
                logger.error(f"工具注销失败: {tool_id}, 错误: {e}")
                return False
    
    def get_tool(self, tool_id: str) -> Optional[BaseTool]:
        """获取工具实例
        
        Args:
            tool_id: 工具ID
            
        Returns:
            工具实例，如果不存在返回None
        """
        # 先从缓存获取
        if tool_id in self._tools:
            return self._tools[tool_id]
        
        # 从数据库加载
        try:
            tool_config = self.db.get(ToolConfig, uuid.UUID(tool_id))
            if not tool_config or not tool_config.status == ToolStatus.ACTIVE.value:
                return None
            
            # 根据工具类型加载实例
            tool_instance = self._load_tool_instance(tool_config)
            if tool_instance:
                self._tools[tool_id] = tool_instance
                return tool_instance
            
        except Exception as e:
            logger.error(f"加载工具失败: {tool_id}, 错误: {e}")
        
        return None
    
    def list_tools(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        tool_type: Optional[ToolType] = None,
        status: Optional[ToolStatus] = None,
        tags: Optional[List[str]] = None
    ) -> List[ToolInfo]:
        """列出工具
        
        Args:
            tenant_id: 租户ID过滤
            tool_type: 工具类型过滤
            status: 工具状态过滤
            tags: 标签过滤
            
        Returns:
            工具信息列表
        """
        try:
            query = self.db.query(ToolConfig)
            
            # 应用过滤条件
            if tenant_id:
                # 返回全局工具（tenant_id为空）和该租户的工具
                query = query.filter(
                    or_(
                        ToolConfig.tenant_id == tenant_id,
                        ToolConfig.tenant_id.is_(None)
                    )
                )
            
            if tool_type:
                query = query.filter(ToolConfig.tool_type == tool_type.value)
            
            if status == ToolStatus.ACTIVE:
                query = query.filter(ToolConfig.is_enabled == True)
            elif status == ToolStatus.INACTIVE:
                query = query.filter(ToolConfig.is_enabled == False)
            
            if tags:
                for tag in tags:
                    query = query.filter(ToolConfig.tags.contains([tag]))
            
            tool_configs = query.all()
            
            # 转换为ToolInfo
            tool_infos = []
            for config in tool_configs:
                tool_info = ToolInfo(
                    id=str(config.id),
                    name=config.name,
                    description=config.description or "",
                    tool_type=ToolType(config.tool_type),
                    version=config.version,
                    status=ToolStatus.ACTIVE if config.is_enabled else ToolStatus.INACTIVE,
                    tags=config.tags or [],
                    tenant_id=str(config.tenant_id) if config.tenant_id else None
                )
                
                # 尝试获取参数信息
                tool_instance = self.get_tool(str(config.id))
                if tool_instance:
                    tool_info.parameters = tool_instance.parameters
                
                tool_infos.append(tool_info)
            
            return tool_infos
            
        except Exception as e:
            logger.error(f"列出工具失败, 错误: {e}")
            return []
    
    async def update_tool_status(self, tool_id: str, status: ToolStatus) -> bool:
        """更新工具状态
        
        Args:
            tool_id: 工具ID
            status: 新状态
            
        Returns:
            更新是否成功
        """
        try:
            tool_config = self.db.get(ToolConfig, uuid.UUID(tool_id))
            if not tool_config:
                logger.warning(f"工具不存在: {tool_id}")
                return False
            
            # 更新状态
            if status == ToolStatus.ACTIVE:
                tool_config.is_enabled = True
            elif status == ToolStatus.INACTIVE:
                tool_config.is_enabled = False
            
            self.db.commit()
            
            # 更新缓存中的工具状态
            if tool_id in self._tools:
                self._tools[tool_id].status = status
            
            logger.info(f"工具状态更新成功: {tool_id} -> {status}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"工具状态更新失败: {tool_id}, 错误: {e}")
            return False
    
    def _load_tool_instance(self, tool_config: type[ToolConfig] | None) -> Optional[BaseTool]:
        """从配置加载工具实例
        
        Args:
            tool_config: 工具配置
            
        Returns:
            工具实例
        """
        try:
            if tool_config.tool_type == ToolType.BUILTIN.value:
                # 加载内置工具
                builtin_config = self.db.query(BuiltinToolConfig).filter(
                    BuiltinToolConfig.id == tool_config.id
                ).first()
                
                if builtin_config and builtin_config.tool_class in self._tool_classes:
                    tool_class = self._tool_classes[builtin_config.tool_class]
                    config = {
                        **tool_config.config_data,
                        "parameters": builtin_config.parameters,
                        "tenant_id": str(tool_config.tenant_id) if tool_config.tenant_id else None,
                        "version": tool_config.version,
                        "tags": tool_config.tags
                    }
                    return tool_class(str(tool_config.id), config)
            
            elif tool_config.tool_type == ToolType.CUSTOM.value:
                # 加载自定义工具
                try:
                    custom_config = self.db.query(CustomToolConfig).filter(
                        CustomToolConfig.id == tool_config.id
                    ).first()
                    
                    if custom_config:
                        config = {
                            **tool_config.config_data,
                            "schema_url": custom_config.schema_url,
                            "schema_content": custom_config.schema_content,
                            "auth_type": custom_config.auth_type,
                            "auth_config": custom_config.auth_config,
                            "base_url": custom_config.base_url,
                            "timeout": custom_config.timeout,
                            "tenant_id": str(tool_config.tenant_id) if tool_config.tenant_id else None,
                            "version": tool_config.version,
                            "tags": tool_config.tags
                        }
                        return CustomTool(str(tool_config.id), config)
                except ImportError as e:
                    logger.error(f"无法导入自定义工具模块: {e}")
            
            elif tool_config.tool_type == ToolType.MCP.value:
                # 加载MCP工具
                try:
                    mcp_config = self.db.query(MCPToolConfig).filter(
                        MCPToolConfig.id == tool_config.id
                    ).first()
                    
                    if mcp_config:
                        config = {
                            **tool_config.config_data,
                            "server_url": mcp_config.server_url,
                            "connection_config": mcp_config.connection_config,
                            "available_tools": mcp_config.available_tools,
                            "tenant_id": str(tool_config.tenant_id) if tool_config.tenant_id else None,
                            "version": tool_config.version,
                            "tags": tool_config.tags
                        }
                        return MCPTool(str(tool_config.id), config)
                except ImportError as e:
                    logger.error(f"无法导入MCP工具模块: {e}")
            
        except Exception as e:
            logger.error(f"加载工具实例失败: {tool_config.id}, 错误: {e}")
        
        return None
    
    def get_tool_statistics(self, tenant_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        """获取工具统计信息
        
        Args:
            tenant_id: 租户ID
            
        Returns:
            统计信息字典
        """
        try:
            query = self.db.query(ToolConfig)
            if tenant_id:
                query = query.filter(ToolConfig.tenant_id == tenant_id)
            
            total_tools = query.count()
            active_tools = query.filter(ToolConfig.is_enabled == True).count()
            
            # 按类型统计
            type_stats = {}
            for tool_type in ToolType:
                count = query.filter(ToolConfig.tool_type == tool_type.value).count()
                type_stats[tool_type.value] = count
            
            return {
                "total_tools": total_tools,
                "active_tools": active_tools,
                "inactive_tools": total_tools - active_tools,
                "by_type": type_stats
            }
            
        except Exception as e:
            logger.error(f"获取工具统计失败, 错误: {e}")
            return {}
    
    def clear_cache(self):
        """清空工具缓存"""
        self._tools.clear()
        logger.info("工具缓存已清空")