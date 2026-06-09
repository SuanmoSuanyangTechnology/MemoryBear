"""
工作流数据访问层
"""

import uuid
from typing import Any, Annotated, Literal
from sqlalchemy.orm import Session
from sqlalchemy import desc, select, delete
from fastapi import Depends

from app.models.workflow_model import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNodeExecution,
    WorkflowNodeCache,
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

    def list_active(self) -> list[WorkflowConfig]:
        """获取所有启用中的工作流配置。"""
        stmt = select(WorkflowConfig).where(WorkflowConfig.is_active.is_(True))
        return list(self.db.execute(stmt).scalars())
    
    def create_or_update(
        self,
        app_id: uuid.UUID,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        variables: list[dict[str, Any]] | None = None,
        environment_variables: list[dict[str, Any]] | None = None,
        execution_config: dict[str, Any] | None = None,
        features: dict[str, Any] | None = None,
        triggers: list[dict[str, Any]] | None = None,
        workflow_type: str = "workflow"
    ) -> WorkflowConfig:
        """创建或更新工作流配置
        
        Args:
            app_id: 应用 ID
            nodes: 节点列表
            edges: 边列表
            variables: 会话变量列表
            environment_variables: 环境变量列表
            execution_config: 执行配置
            features: 功能特性
            triggers: 触发器列表
            workflow_type: 工作流类型
        
        Returns:
            工作流配置
        """
        # 查找现有配置
        existing = self.get_by_app_id(app_id)
        
        if existing:
            # 更新现有配置
            existing.nodes = nodes
            existing.edges = edges
            existing.workflow_type = workflow_type
            if variables is not None:
                existing.variables = variables
            if environment_variables is not None:
                existing.environment_variables = environment_variables
            if execution_config is not None:
                existing.execution_config = execution_config
            if triggers is not None:
                existing.triggers = triggers
            if features is not None:
                existing.features = features
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
                environment_variables=environment_variables or [],
                execution_config=execution_config or {},
                features=features or {},
                triggers=triggers or [],
                workflow_type=workflow_type
            )
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            return config

    def update_trigger_runtime(
        self,
        app_id: uuid.UUID,
        trigger_id: str,
        runtime: dict[str, Any],
    ) -> WorkflowConfig | None:
        """更新指定触发器的运行时状态。"""
        config = self.get_by_app_id(app_id)
        if not config:
            return None

        nodes = list(config.nodes or [])
        updated = False
        for node in nodes:
            from app.core.workflow.nodes.enums import NodeType
            if node.get("type") == NodeType.TRIGGER and node.get("id") == trigger_id:
                node["runtime"] = runtime
                updated = True
                break

        if not updated:
            return None

        config.nodes = nodes
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
        stmt = select(WorkflowExecution).filter(
            WorkflowExecution.app_id == app_id
        ).order_by(
            desc(WorkflowExecution.started_at)
        ).limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars())
    
    def get_by_conversation_id(
        self,
        conversation_id: uuid.UUID,
        status: Literal["running", "completed", "failed"] = None,
        limit_count: int = 50
    ) -> list[WorkflowExecution]:
        """根据会话 ID 获取执行记录列表
        
        Args:
            limit_count:
            conversation_id: 会话 ID
            status: 状态（可选）
        
        Returns:
            执行记录列表
        """
        stmt = select(WorkflowExecution).filter(
            WorkflowExecution.conversation_id == conversation_id
        )
        if status:
            stmt = stmt.filter(WorkflowExecution.status == status)
        stmt = stmt.order_by(desc(WorkflowExecution.started_at)).limit(limit_count)
        return list(self.db.execute(stmt).scalars())
    
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

    def create(self, **kwargs) -> WorkflowNodeExecution:
        node_execution = WorkflowNodeExecution(**kwargs)
        self.db.add(node_execution)
        return node_execution

    def bulk_create(
        self,
        items: list[dict[str, Any]]
    ) -> list[WorkflowNodeExecution]:
        if not items:
            return []

        node_executions = [WorkflowNodeExecution(**item) for item in items]
        self.db.add_all(node_executions)
        return node_executions

    def delete_by_execution_id(self, execution_id: uuid.UUID) -> None:
        stmt = delete(WorkflowNodeExecution).where(
            WorkflowNodeExecution.execution_id == execution_id
        )
        self.db.execute(stmt)
    
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
        stmt = select(WorkflowNodeExecution).filter(
            WorkflowNodeExecution.execution_id == execution_id
        ).order_by(
            WorkflowNodeExecution.execution_order
        )
        return list(self.db.execute(stmt).scalars())
    
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
        stmt = select(WorkflowNodeExecution).filter(
            WorkflowNodeExecution.execution_id == execution_id,
            WorkflowNodeExecution.node_id == node_id
        ).order_by(
            WorkflowNodeExecution.retry_count
        )
        return list(self.db.execute(stmt).scalars())

    def get_latest_by_app_node(
        self,
        app_id: uuid.UUID,
        node_id: str,
        source: str | None = None,
    ) -> WorkflowNodeExecution | None:
        stmt = (
            select(WorkflowNodeExecution)
            .where(
                WorkflowNodeExecution.app_id == app_id,
                WorkflowNodeExecution.node_id == node_id,
            )
            .order_by(
                desc(WorkflowNodeExecution.completed_at).nullslast(),
                desc(WorkflowNodeExecution.created_at),
                desc(WorkflowNodeExecution.started_at),
            )
        )
        if source is None:
            row = self.db.execute(stmt.limit(1)).scalars().first()
            return row

        stmt = stmt.where(
            WorkflowNodeExecution.meta_data["source"].as_string() == source
        ).limit(1)
        return self.db.execute(stmt).scalars().first()


class WorkflowNodeCacheRepository:
    """工作流节点缓存仓储"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, **kwargs) -> WorkflowNodeCache:
        cache = WorkflowNodeCache(**kwargs)
        self.db.add(cache)
        return cache

    def get_active_by_key(
            self,
            app_id: uuid.UUID,
            node_id: str,
            cache_key: str,
    ) -> WorkflowNodeCache | None:
        stmt = (
            select(WorkflowNodeCache)
            .where(
                WorkflowNodeCache.app_id == app_id,
                WorkflowNodeCache.node_id == node_id,
                WorkflowNodeCache.cache_key == cache_key,
                WorkflowNodeCache.status == "active",
            )
            .order_by(desc(WorkflowNodeCache.updated_at), desc(WorkflowNodeCache.created_at))
            .limit(1)
        )
        return self.db.execute(stmt).scalars().first()

    def get_latest_by_node(
            self,
            app_id: uuid.UUID,
            node_id: str,
            include_inactive: bool = False,
    ) -> WorkflowNodeCache | None:
        stmt = select(WorkflowNodeCache).where(
            WorkflowNodeCache.app_id == app_id,
            WorkflowNodeCache.node_id == node_id,
        )
        if not include_inactive:
            stmt = stmt.where(WorkflowNodeCache.status == "active")
        stmt = stmt.order_by(desc(WorkflowNodeCache.updated_at), desc(WorkflowNodeCache.created_at)).limit(1)
        return self.db.execute(stmt).scalars().first()

    def invalidate_by_node(
            self,
            app_id: uuid.UUID,
            node_id: str,
            *,
            invalidated_at,
            statuses: tuple[str, ...] = ("active", "expired"),
    ) -> int:
        stmt = (
            select(WorkflowNodeCache)
            .where(
                WorkflowNodeCache.app_id == app_id,
                WorkflowNodeCache.node_id == node_id,
                WorkflowNodeCache.status.in_(statuses),
            )
        )
        items = list(self.db.execute(stmt).scalars())
        for item in items:
            item.status = "invalidated"
            item.invalidated_at = invalidated_at
        return len(items)

    def invalidate_by_app(
            self,
            app_id: uuid.UUID,
            *,
            invalidated_at,
            statuses: tuple[str, ...] = ("active", "expired"),
            exclude_node_ids: tuple[str, ...] = (),
    ) -> int:
        stmt = (
            select(WorkflowNodeCache)
            .where(
                WorkflowNodeCache.app_id == app_id,
                WorkflowNodeCache.status.in_(statuses),
            )
        )
        if exclude_node_ids:
            stmt = stmt.where(WorkflowNodeCache.node_id.notin_(exclude_node_ids))
        items = list(self.db.execute(stmt).scalars())
        for item in items:
            item.status = "invalidated"
            item.invalidated_at = invalidated_at
        return len(items)

    def list_latest_by_app(
            self,
            app_id: uuid.UUID,
            include_inactive: bool = False,
    ) -> list[WorkflowNodeCache]:
        stmt = select(WorkflowNodeCache).where(
            WorkflowNodeCache.app_id == app_id,
        )
        if not include_inactive:
            stmt = stmt.where(WorkflowNodeCache.status == "active")
        stmt = stmt.order_by(
            WorkflowNodeCache.node_id,
            desc(WorkflowNodeCache.updated_at),
            desc(WorkflowNodeCache.created_at),
        )
        items = list(self.db.execute(stmt).scalars())
        latest_by_node: dict[str, WorkflowNodeCache] = {}
        for item in items:
            if item.node_id not in latest_by_node:
                latest_by_node[item.node_id] = item
        return list(latest_by_node.values())

    def invalidate_expired(self, now) -> int:
        stmt = select(WorkflowNodeCache).where(
            WorkflowNodeCache.status == "active",
            WorkflowNodeCache.expires_at.is_not(None),
            WorkflowNodeCache.expires_at <= now,
        )
        items = list(self.db.execute(stmt).scalars())
        for item in items:
            item.status = "expired"
            item.invalidated_at = now
        return len(items)


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


def get_workflow_node_cache_repository(
    db: Annotated[Session, Depends(get_db)]
) -> WorkflowNodeCacheRepository:
    """获取工作流节点缓存仓储（依赖注入）"""
    return WorkflowNodeCacheRepository(db)
