"""
工作流数据访问层
"""

import uuid
from typing import Any, Annotated, Literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import desc, select, delete, func, or_
from fastapi import Depends

from app.models.workflow_model import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNodeExecution,
    WorkflowNodeCache,
)
from app.db import get_db

# 单节点调试记录在 meta_data.source 中的标识
SINGLE_NODE_DEBUG_SOURCE = "single_node_debug"

# 完整工作流节点行的保留策略清理 SQL（异步队列落库路径使用）。
#
# 语义：每次成功写入节点投影后，按 started_at / created_at / id 确定
# 同一 app_id + workflow_config_id 下真正最新的 execution，只保留它的节点行。
#
# 不能简单保留本次写入的 execution：较旧的 waiting_human 执行可能在较新的执行
# 之后恢复并再次写入；如果固定保留本次 execution，会错误淘汰真正最新的执行。
# 本清理只处理 execution_id 非空的整图执行行，不影响单节点调试行。
NODE_EXECUTION_RETENTION_SQL = """
WITH current_scope AS (
    SELECT app_id, workflow_config_id
    FROM workflow_executions
    WHERE id = CAST(:eid AS uuid)
),
latest_execution AS (
    SELECT candidate.id
    FROM workflow_executions AS candidate
    JOIN current_scope AS scope
      ON candidate.app_id = scope.app_id
     AND candidate.workflow_config_id = scope.workflow_config_id
    ORDER BY candidate.started_at DESC,
             candidate.created_at DESC,
             candidate.id DESC
    LIMIT 1
)
DELETE FROM workflow_node_executions AS stale
USING workflow_executions AS stale_exec,
      current_scope AS scope
WHERE stale.execution_id = stale_exec.id
  AND stale_exec.app_id = scope.app_id
  AND stale_exec.workflow_config_id = scope.workflow_config_id
  AND stale_exec.id <> (SELECT id FROM latest_execution)
"""


class WorkflowConfigRepository:
    """工作流配置仓储"""
    
    def __init__(self, db: Session | AsyncSession):
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

    async def get_by_app_id_async(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """根据应用 ID 异步获取工作流配置"""
        result = await self.db.execute(
            select(WorkflowConfig).where(
                WorkflowConfig.app_id == app_id,
                WorkflowConfig.is_active.is_(True)
            ).limit(1)
        )
        return result.scalar_one_or_none()

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
        from app.core.workflow.nodes.enums import NodeType
        for node in nodes:
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
    
    def __init__(self, db: Session | AsyncSession):
        self.db = db

    @staticmethod
    def _visible_in_app_log_filter():
        """排除 Agent 作为内部工具调用产生的工作流子执行。

        历史记录没有 ``meta_data.source``，必须保留；否则发布前已有的正常调用
        会被 SQL 的 NULL 比较意外过滤掉。
        """
        source = WorkflowExecution.meta_data["source"].as_string()
        return or_(source.is_(None), source != "tool")
    
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

    def list_for_app_log(
        self,
        app_id: uuid.UUID,
        page: int,
        pagesize: int,
        is_draft: bool | None = None,
        start_date=None,
        end_date=None,
    ) -> tuple[list[WorkflowExecution], int]:
        """分页读取一个应用的工作流执行日志。"""
        filters = [
            WorkflowExecution.app_id == app_id,
            self._visible_in_app_log_filter(),
        ]
        if is_draft is True:
            filters.append(WorkflowExecution.release_id.is_(None))
        elif is_draft is False:
            filters.append(WorkflowExecution.release_id.is_not(None))
        if start_date:
            filters.append(WorkflowExecution.started_at >= start_date)
        if end_date:
            filters.append(WorkflowExecution.started_at <= end_date)

        statement = (
            select(WorkflowExecution)
            .where(*filters)
            .order_by(desc(WorkflowExecution.started_at), desc(WorkflowExecution.created_at))
            .limit(pagesize)
            .offset((page - 1) * pagesize)
        )
        count_statement = select(func.count()).select_from(WorkflowExecution).where(*filters)
        return (
            list(self.db.execute(statement).scalars()),
            int(self.db.execute(count_statement).scalar_one()),
        )

    def get_for_app_log(
        self,
        app_id: uuid.UUID,
        execution_id: str,
    ) -> WorkflowExecution | None:
        """按业务执行 ID 查询应用内的一条执行记录。"""
        statement = select(WorkflowExecution).where(
            WorkflowExecution.app_id == app_id,
            WorkflowExecution.execution_id == execution_id,
            self._visible_in_app_log_filter(),
        )
        return self.db.execute(statement).scalars().first()
    
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
    
    def __init__(self, db: Session | AsyncSession):
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

    def delete_stale_workflow_executions(
        self,
        *,
        app_id: uuid.UUID,
        workflow_config_id: uuid.UUID,
        keep_execution_id: uuid.UUID,
    ) -> int:
        """只保留同一工作流真正最新一次整图执行的节点记录。

        最新执行按 started_at、created_at、id 倒序确定，不要求执行已进入终态。
        这样 waiting_human 记录也受保留策略约束，并避免较旧的人工介入执行
        稍后恢复时淘汰更新执行的节点记录。单节点调试行的 execution_id 为空，
        不会被本方法删除。
        """
        current_execution_exists = (
            select(WorkflowExecution.id)
            .where(
                WorkflowExecution.id == keep_execution_id,
                WorkflowExecution.app_id == app_id,
                WorkflowExecution.workflow_config_id == workflow_config_id,
            )
            .exists()
        )
        latest_execution_id = (
            select(WorkflowExecution.id)
            .where(
                WorkflowExecution.app_id == app_id,
                WorkflowExecution.workflow_config_id == workflow_config_id,
            )
            .order_by(
                desc(WorkflowExecution.started_at),
                desc(WorkflowExecution.created_at),
                desc(WorkflowExecution.id),
            )
            .limit(1)
            .scalar_subquery()
        )
        stale_execution_ids = select(WorkflowExecution.id).where(
            WorkflowExecution.app_id == app_id,
            WorkflowExecution.workflow_config_id == workflow_config_id,
            WorkflowExecution.id != latest_execution_id,
        )
        stmt = delete(WorkflowNodeExecution).where(
            current_execution_exists,
            WorkflowNodeExecution.execution_id.in_(stale_execution_ids),
        )
        result = self.db.execute(
            stmt,
            execution_options={"synchronize_session": False},
        )
        return result.rowcount or 0

    def delete_stale_single_node_debug(
        self,
        *,
        app_id: uuid.UUID,
        workflow_config_id: uuid.UUID,
        node_id: str,
        keep_id: uuid.UUID,
    ) -> int:
        """清理同一节点除 keep_id 外的历史单节点调试记录。

        过滤条件必须同时满足，否则会误删完整工作流的节点行：
          execution_id IS NULL
          meta_data['source'] == 'single_node_debug'
          id != keep_id

        Args:
            app_id: 应用 ID
            workflow_config_id: 工作流配置 ID
            node_id: 节点 ID
            keep_id: 需要保留的记录 ID（本次调试新写入的行）

        Returns:
            删除的行数
        """
        stmt = delete(WorkflowNodeExecution).where(
            WorkflowNodeExecution.app_id == app_id,
            WorkflowNodeExecution.workflow_config_id == workflow_config_id,
            WorkflowNodeExecution.node_id == node_id,
            WorkflowNodeExecution.execution_id.is_(None),
            WorkflowNodeExecution.meta_data["source"].as_string() == SINGLE_NODE_DEBUG_SOURCE,
            WorkflowNodeExecution.id != keep_id,
        )
        result = self.db.execute(
            stmt,
            execution_options={"synchronize_session": False},
        )
        return result.rowcount or 0

    
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

    def __init__(self, db: Session | AsyncSession):
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
