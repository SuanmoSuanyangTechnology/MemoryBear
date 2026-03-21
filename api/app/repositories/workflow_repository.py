"""
工作流数据访问层
"""

import uuid
from typing import Any, Annotated
from sqlalchemy.orm import Session
from sqlalchemy import desc
from fastapi import Depends

from app.models.workflow_model import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNodeExecution
)
from app.db import get_db


class WorkflowConfigRepository:
    """工作流配置仓储"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_by_app_id(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """根据应用 ID 获取工作流配置
        
        Args:
            app_id: 应用 ID
        
        Returns:
            工作流配置或 None
        """
        return self.db.query(WorkflowConfig).filter(
            WorkflowConfig.app_id == app_id,
            WorkflowConfig.is_active.is_(True)
        ).first()
    
    def create_or_update(
        self,
        app_id: uuid.UUID,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        variables: list[dict[str, Any]] | None = None,
        execution_config: dict[str, Any] | None = None,
        triggers: list[dict[str, Any]] | None = None
    ) -> WorkflowConfig:
        """创建或更新工作流配置
        
        Args:
            app_id: 应用 ID
            nodes: 节点列表
            edges: 边列表
            variables: 变量列表
            execution_config: 执行配置
            triggers: 触发器列表
        
        Returns:
            工作流配置
        """
        # 查找现有配置
        existing = self.get_by_app_id(app_id)
        
        if existing:
            # 更新现有配置
            existing.nodes = nodes
            existing.edges = edges
            if variables is not None:
                existing.variables = variables
            if execution_config is not None:
                existing.execution_config = execution_config
            if triggers is not None:
                existing.triggers = triggers
            self.db.commit()
            self.db.refresh(existing)
            return existing
        else:
            # 创建新配置
            config = WorkflowConfig(
                app_id=app_id,
                nodes=nodes,
                edges=edges,
                variables=variables or [],
                execution_config=execution_config or {},
                triggers=triggers or []
            )
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            return config


class WorkflowExecutionRepository:
    """工作流执行记录仓储"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_by_execution_id(self, execution_id: str) -> WorkflowExecution | None:
        """根据执行 ID 获取执行记录
        
        Args:
            execution_id: 执行 ID
        
        Returns:
            执行记录或 None
        """
        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.execution_id == execution_id
        ).first()
    
    def get_by_app_id(
        self,
        app_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0
    ) -> list[WorkflowExecution]:
        """根据应用 ID 获取执行记录列表
        
        Args:
            app_id: 应用 ID
            limit: 返回数量限制
            offset: 偏移量
        
        Returns:
            执行记录列表
        """
        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.app_id == app_id
        ).order_by(
            desc(WorkflowExecution.started_at)
        ).limit(limit).offset(offset).all()
    
    def get_by_conversation_id(
        self,
        conversation_id: uuid.UUID
    ) -> list[WorkflowExecution]:
        """根据会话 ID 获取执行记录列表
        
        Args:
            conversation_id: 会话 ID
        
        Returns:
            执行记录列表
        """
        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.conversation_id == conversation_id
        ).order_by(
            desc(WorkflowExecution.started_at)
        ).all()
    
    def count_by_app_id(self, app_id: uuid.UUID) -> int:
        """统计应用的执行次数
        
        Args:
            app_id: 应用 ID
        
        Returns:
            执行次数
        """
        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.app_id == app_id
        ).count()
    
    def count_by_status(self, app_id: uuid.UUID, status: str) -> int:
        """统计指定状态的执行次数
        
        Args:
            app_id: 应用 ID
            status: 状态
        
        Returns:
            执行次数
        """
        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.app_id == app_id,
            WorkflowExecution.status == status
        ).count()


class WorkflowNodeExecutionRepository:
    """工作流节点执行记录仓储"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_by_execution_id(
        self,
        execution_id: uuid.UUID
    ) -> list[WorkflowNodeExecution]:
        """根据执行 ID 获取节点执行记录列表
        
        Args:
            execution_id: 执行 ID
        
        Returns:
            节点执行记录列表（按执行顺序排序）
        """
        return self.db.query(WorkflowNodeExecution).filter(
            WorkflowNodeExecution.execution_id == execution_id
        ).order_by(
            WorkflowNodeExecution.execution_order
        ).all()
    
    def get_by_node_id(
        self,
        execution_id: uuid.UUID,
        node_id: str
    ) -> list[WorkflowNodeExecution]:
        """根据节点 ID 获取节点执行记录（可能有多次重试）
        
        Args:
            execution_id: 执行 ID
            node_id: 节点 ID
        
        Returns:
            节点执行记录列表
        """
        return self.db.query(WorkflowNodeExecution).filter(
            WorkflowNodeExecution.execution_id == execution_id,
            WorkflowNodeExecution.node_id == node_id
        ).order_by(
            WorkflowNodeExecution.retry_count
        ).all()


# ==================== 依赖注入函数 ====================

def get_workflow_config_repository(
    db: Annotated[Session, Depends(get_db)]
) -> WorkflowConfigRepository:
    """获取工作流配置仓储（依赖注入）"""
    return WorkflowConfigRepository(db)


def get_workflow_execution_repository(
    db: Annotated[Session, Depends(get_db)]
) -> WorkflowExecutionRepository:
    """获取工作流执行记录仓储（依赖注入）"""
    return WorkflowExecutionRepository(db)


def get_workflow_node_execution_repository(
    db: Annotated[Session, Depends(get_db)]
) -> WorkflowNodeExecutionRepository:
    """获取工作流节点执行记录仓储（依赖注入）"""
    return WorkflowNodeExecutionRepository(db)
