"""Workflow config table read paths (mirrors the monolith's
``workflow_repository`` reads and the create/update write used by the
definition plane) plus the execution-log reads used by the app-log face."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ..models.workflow_model import (
    WorkflowConfig,
    WorkflowExecution,
    WorkflowNodeExecution,
)


class WorkflowConfigRepository:
    """Workflow config repository"""

    def __init__(self, db: Session):
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


class WorkflowNodeExecutionRepository:
    """Workflow node-execution log reads (branch-view face)."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_execution_id(self, execution_id: UUID) -> list[WorkflowNodeExecution]:
        """Node executions of one execution record in execution order."""

        statement = (
            select(WorkflowNodeExecution)
            .filter(WorkflowNodeExecution.execution_id == execution_id)
            .order_by(WorkflowNodeExecution.execution_order)
        )
        return list(self.db.execute(statement).scalars())


__all__ = [
    "WorkflowConfigRepository",
    "WorkflowExecutionRepository",
    "WorkflowNodeExecutionRepository",
]
