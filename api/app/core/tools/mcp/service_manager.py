"""MCP服务管理器 - 简化版本"""
import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.tool_model import MCPToolConfig, ToolConfig, ToolType, ToolStatus
from app.core.logging_config import get_business_logger
from app.core.tools.mcp.base import MCPToolManager

logger = get_business_logger()


class MCPServiceManager:
    """MCP服务管理器 - 简化版本，主要用于工具创建"""
    
    def __init__(self, db: Session = None):
        self.db = db
        self.tool_manager = MCPToolManager(db) if db else None
    
    async def create_mcp_tool(
        self,
        server_url: str,
        connection_config: Dict[str, Any],
        tenant_id: uuid.UUID,
        tool_name: str,
        service_name: str = None
    ) -> Tuple[bool, str, Optional[str]]:
        """创建单个MCP工具
        
        Args:
            server_url: 服务器URL
            connection_config: 连接配置
            tenant_id: 租户ID
            tool_name: 具体工具名称
            service_name: 服务名称
            
        Returns:
            (是否成功, 工具ID或错误信息, 错误详情)
        """
        try:
            if not service_name:
                service_name = f"mcp_{tool_name}"
            
            # 创建工具配置
            tool_config = ToolConfig(
                name=service_name,
                description=f"MCP工具: {tool_name}",
                tool_type=ToolType.MCP.value,
                tenant_id=tenant_id,
                status=ToolStatus.AVAILABLE.value,
                config_data={
                    "server_url": server_url,
                    "connection_config": connection_config,
                    "tool_name": tool_name
                }
            )
            
            self.db.add(tool_config)
            self.db.flush()
            
            # 创建MCP特定配置
            mcp_config = MCPToolConfig(
                id=tool_config.id,
                server_url=server_url,
                connection_config=connection_config,
                available_tools=[tool_name],
                health_status="unknown",
                last_health_check=datetime.now()
            )
            
            self.db.add(mcp_config)
            self.db.commit()
            
            logger.info(f"MCP工具创建成功: {tool_config.id} ({tool_name})")
            return True, str(tool_config.id), None
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"创建MCP工具失败: {tool_name}, 错误: {e}")
            return False, "创建失败", str(e)
    
    def get_tool_manager(self) -> MCPToolManager:
        """获取工具管理器实例"""
        return self.tool_manager