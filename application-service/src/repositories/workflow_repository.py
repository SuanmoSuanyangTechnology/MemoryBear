"""Workflow config table read paths (mirrors the monolith's
``workflow_repository`` reads and the create/update write used by the
definition plane) plus the execution-log reads used by the app-log face."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.workflow_model import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNodeCache,
    WorkflowNodeExecution,
)

# Retention SQL for full-graph node rows, shared with the async queue persist
# path. After each node-projection write the table keeps only the node rows of
# the truly latest execution (by started_at / created_at / id) under the same
# app_id + workflow_config_id. Keeping the just-written execution instead
# would mis-evict: an older waiting_human execution can resume after a newer
# one and write again. Only rows with a non-null execution_id are matched, so
# single-node debug rows are never affected.
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
    """Workflow config repository"""

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def get_by_app_id(self, app_id: UUID) -> WorkflowConfig | None:
        """Get the workflow config by app ID

        Args:
            app_id: App ID

        Returns:
            The workflow config or None
        """
        return self.db.query(WorkflowConfig).filter(
            WorkflowConfig.app_id == app_id,
            WorkflowConfig.is_active.is_(True)
        ).first()

    async def get_by_app_id_async(self, app_id: UUID) -> WorkflowConfig | None:
        """Async read of the active workflow config (runtime fetch path)."""

        result = await self.db.execute(
            select(WorkflowConfig).where(
                WorkflowConfig.app_id == app_id,
                WorkflowConfig.is_active.is_(True)
            ).limit(1)
        )
        return result.scalar_one_or_none()

    def create_or_update(
        self,
        app_id: UUID,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        variables: list[dict[str, Any]] | None = None,
        environment_variables: list[dict[str, Any]] | None = None,
        execution_config: dict[str, Any] | None = None,
        features: dict[str, Any] | None = None,
        triggers: list[dict[str, Any]] | None = None,
        workflow_type: str = "workflow"
    ) -> WorkflowConfig:
        """Create or update the workflow config

        Args:
            app_id: App ID
            nodes: Node list
            edges: Edge list
            variables: Conversation variable list
            environment_variables: Environment variable list
            execution_config: Execution config
            features: Feature toggles
            triggers: Trigger list
            workflow_type: Workflow type

        Returns:
            The workflow config
        """
        # Look up the existing config
        existing = self.get_by_app_id(app_id)

        if existing:
            # Update the existing config
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
            # Create a new config
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


class WorkflowExecutionRepository:
    """Workflow execution log read paths (app-log face)."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _visible_in_app_log_filter():
        """Exclude workflow sub-executions spawned as internal tool calls of an
        agent. Records without ``meta_data.source`` predate the marker and must
        stay visible; a plain SQL NULL comparison would drop them."""

        source = WorkflowExecution.meta_data["source"].as_string()
        return or_(source.is_(None), source != "tool")

    def list_for_app_log(
        self,
        app_id: UUID,
        page: int,
        pagesize: int,
        is_draft: bool | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[WorkflowExecution], int]:
        """Paginated workflow execution log of one app; returns (rows, total)."""

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
        app_id: UUID,
        execution_id: str,
    ) -> WorkflowExecution | None:
        """One execution record of the app by its business execution id."""

        statement = select(WorkflowExecution).where(
            WorkflowExecution.app_id == app_id,
            WorkflowExecution.execution_id == execution_id,
            self._visible_in_app_log_filter(),
        )
        return self.db.execute(statement).scalars().first()

    def get_by_execution_id(self, execution_id: str) -> WorkflowExecution | None:
        """One execution record by its business execution id (no app/visibility
        scoping: the branch view resolves the id carried in message metadata)."""

        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.execution_id == execution_id
        ).first()

    def get_by_app_id(
        self,
        app_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowExecution]:
        """Execution records of one app, newest started first."""

        stmt = (
            select(WorkflowExecution)
            .filter(WorkflowExecution.app_id == app_id)
            .order_by(desc(WorkflowExecution.started_at))
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars())

    def get_by_conversation_id(
        self,
        conversation_id: UUID,
        status: str | None = None,
        limit_count: int = 50,
    ) -> list[WorkflowExecution]:
        """Execution records of one conversation, newest started first."""

        stmt = select(WorkflowExecution).filter(
            WorkflowExecution.conversation_id == conversation_id
        )
        if status:
            stmt = stmt.filter(WorkflowExecution.status == status)
        stmt = stmt.order_by(desc(WorkflowExecution.started_at)).limit(limit_count)
        return list(self.db.execute(stmt).scalars())

    def count_by_app_id(self, app_id: UUID) -> int:
        """Number of executions of one app."""

        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.app_id == app_id
        ).count()

    def count_by_status(self, app_id: UUID, status: str) -> int:
        """Number of executions of one app in the given status."""

        return self.db.query(WorkflowExecution).filter(
            WorkflowExecution.app_id == app_id,
            WorkflowExecution.status == status,
        ).count()


class WorkflowNodeExecutionRepository:
    """Workflow node-execution log reads (branch-view face)."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, **kwargs) -> WorkflowNodeExecution:
        node_execution = WorkflowNodeExecution(**kwargs)
        self.db.add(node_execution)
        return node_execution

    def bulk_create(
        self,
        items: list[dict[str, Any]],
    ) -> list[WorkflowNodeExecution]:
        if not items:
            return []

        node_executions = [WorkflowNodeExecution(**item) for item in items]
        self.db.add_all(node_executions)
        return node_executions

    def delete_by_execution_id(self, execution_id: UUID) -> None:
        stmt = delete(WorkflowNodeExecution).where(
            WorkflowNodeExecution.execution_id == execution_id
        )
        self.db.execute(stmt)

    def delete_stale_workflow_executions(
        self,
        *,
        app_id: UUID,
        workflow_config_id: UUID,
        keep_execution_id: UUID,
    ) -> int:
        """Keep node rows of only the truly latest full-graph execution.

        Latest is decided by started_at / created_at / id descending without
        requiring a terminal status, so waiting_human rows are subject to the
        same retention and an older human-intervention execution resuming
        later cannot evict rows of a newer execution. Single-node debug rows
        carry a null execution_id and are never matched here.
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
        app_id: UUID,
        workflow_config_id: UUID,
        node_id: str,
        keep_id: UUID,
    ) -> int:
        """Drop the node's previous single-node debug rows, keeping keep_id.

        All filters must hold together or full-workflow node rows would be
        deleted by mistake: execution_id IS NULL, the debug source marker and
        a different row id.
        """

        stmt = delete(WorkflowNodeExecution).where(
            WorkflowNodeExecution.app_id == app_id,
            WorkflowNodeExecution.workflow_config_id == workflow_config_id,
            WorkflowNodeExecution.node_id == node_id,
            WorkflowNodeExecution.execution_id.is_(None),
            WorkflowNodeExecution.meta_data["source"].as_string()
            == "single_node_debug",
            WorkflowNodeExecution.id != keep_id,
        )
        result = self.db.execute(
            stmt,
            execution_options={"synchronize_session": False},
        )
        return result.rowcount or 0

    def get_by_execution_id(self, execution_id: UUID) -> list[WorkflowNodeExecution]:
        """Node executions of one execution record in execution order."""

        statement = (
            select(WorkflowNodeExecution)
            .filter(WorkflowNodeExecution.execution_id == execution_id)
            .order_by(WorkflowNodeExecution.execution_order)
        )
        return list(self.db.execute(statement).scalars())

    def get_latest_by_app_node(
        self,
        app_id: UUID,
        node_id: str,
        source: str | None = None,
    ) -> WorkflowNodeExecution | None:
        """Newest node row of one app+node, optionally scoped to the source
        marker in ``meta_data`` (``workflow_execution`` / debug source)."""

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
    """Workflow node debug-cache repository (frozen port of the host class).

    Always driven by a sync session facade (node-cache callers run it through
    ``AsyncSession.run_sync`` or the sync context helper).
    """

    def __init__(self, db: Session):
        self.db = db

    def create(self, **kwargs) -> WorkflowNodeCache:
        cache = WorkflowNodeCache(**kwargs)
        self.db.add(cache)
        return cache

    def get_active_by_key(
            self,
            app_id: UUID,
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
            app_id: UUID,
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
            app_id: UUID,
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
            app_id: UUID,
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
            app_id: UUID,
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


__all__ = [
    "WorkflowConfigRepository",
    "WorkflowExecutionRepository",
    "WorkflowNodeCacheRepository",
    "WorkflowNodeExecutionRepository",
]
