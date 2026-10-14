"""Workflow config table read paths (mirrors the monolith's
``workflow_repository`` reads and the create/update write used by the
definition plane)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from ..models.workflow_model import WorkflowConfig


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


__all__ = ["WorkflowConfigRepository"]
