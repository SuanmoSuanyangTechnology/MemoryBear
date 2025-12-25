"""工具数据访问层"""
import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.repositories.base_repository import BaseRepository
from app.models.tool_model import (
    ToolConfig, BuiltinToolConfig, CustomToolConfig, MCPToolConfig,
    ToolExecution, ToolType, ToolStatus
)


class ToolRepository:
    """工具仓储类"""

    @staticmethod
    def find_by_tenant(
        db: Session,
        tenant_id: uuid.UUID,
        name: Optional[str] = None,
        tool_type: Optional[ToolType] = None,
        status: Optional[ToolStatus] = None,
        is_enabled: Optional[bool] = None
    ) -> List[ToolConfig]:
        """根据租户查找工具"""
        query = db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id
        )
        
        if name:
            query = query.filter(ToolConfig.name.ilike(f"%{name}%"))
        if tool_type:
            query = query.filter(ToolConfig.tool_type == tool_type.value)
        if status:
            query = query.filter(ToolConfig.status == status.value)
        if is_enabled is not None:
            query = query.filter(ToolConfig.is_enabled == is_enabled)
        
        return query.all()

    @staticmethod
    def find_by_id_and_tenant(db:Session, tool_id: uuid.UUID, tenant_id: uuid.UUID) -> Optional[ToolConfig]:
        """根据ID和租户查找工具"""
        return db.query(ToolConfig).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == tenant_id
        ).first()

    @staticmethod
    def count_by_tenant(db: Session, tenant_id: uuid.UUID) -> int:
        """统计租户工具数量"""
        return db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id
        ).count()

    @staticmethod
    def get_status_statistics(db: Session, tenant_id: uuid.UUID) -> List[tuple]:
        """获取状态统计"""
        return db.query(
            ToolConfig.status,
            func.count(ToolConfig.id).label('count')
        ).filter(
            ToolConfig.tenant_id == tenant_id
        ).group_by(ToolConfig.status).all()

    @staticmethod
    def get_type_statistics(db: Session, tenant_id: uuid.UUID) -> List[tuple]:
        """获取类型统计"""
        return db.query(
            ToolConfig.tool_type,
            func.count(ToolConfig.id).label('count')
        ).filter(
            ToolConfig.tenant_id == tenant_id
        ).group_by(ToolConfig.tool_type).all()

    @staticmethod
    def count_enabled_by_tenant(db: Session, tenant_id: uuid.UUID) -> int:
        """统计租户启用的工具数量"""
        return db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_enabled == True
        ).count()

    @staticmethod
    def exists_builtin_for_tenant(db: Session, tenant_id: uuid.UUID) -> bool:
        """检查租户是否已有内置工具"""
        return db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.tool_type == ToolType.BUILTIN.value
        ).count() > 0


class BuiltinToolRepository:
    """内置工具仓储类"""

    @staticmethod
    def find_by_tool_id(db: Session, tool_id: uuid.UUID) -> Optional[BuiltinToolConfig]:
        """根据工具ID查找内置工具配置"""
        return db.query(BuiltinToolConfig).filter(
            BuiltinToolConfig.id == tool_id
        ).first()


class CustomToolRepository:
    """自定义工具仓储类"""

    @staticmethod
    def find_by_tool_id(db: Session, tool_id: uuid.UUID) -> Optional[CustomToolConfig]:
        """根据工具ID查找自定义工具配置"""
        return db.query(CustomToolConfig).filter(
            CustomToolConfig.id == tool_id
        ).first()


class MCPToolRepository:
    """MCP工具仓储类"""

    @staticmethod
    def find_by_tool_id(db: Session, tool_id: uuid.UUID) -> Optional[MCPToolConfig]:
        """根据工具ID查找MCP工具配置"""
        return db.query(MCPToolConfig).filter(
            MCPToolConfig.id == tool_id
        ).first()

    @staticmethod
    def find_error_connections(db: Session) -> List[MCPToolConfig]:
        """查找连接错误的MCP工具"""
        return db.query(MCPToolConfig).filter(
            MCPToolConfig.connection_status == "error"
        ).all()


class ToolExecutionRepository:
    """工具执行仓储类"""

    @staticmethod
    def find_by_execution_id(db: Session, execution_id: str) -> Optional[ToolExecution]:
        """根据执行ID查找执行记录"""
        return db.query(ToolExecution).filter(
            ToolExecution.execution_id == execution_id
        ).first()

    @staticmethod
    def find_by_tool_and_tenant(
        db: Session,
        tool_id: uuid.UUID,
        tenant_id: uuid.UUID,
        limit: int = 100
    ) -> List[ToolExecution]:
        """根据工具和租户查找执行记录"""
        return db.query(ToolExecution).join(
            ToolConfig, ToolExecution.tool_config_id == ToolConfig.id
        ).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == tenant_id
        ).order_by(ToolExecution.started_at.desc()).limit(limit).all()