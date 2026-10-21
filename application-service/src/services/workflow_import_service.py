"""DSL/workflow import service (port of the host ``WorkflowImportService``).

Split adaptation (registered deviation): the management plane is synchronous,
so the two-phase parse/save flow runs on sync Redis calls through the
request-scoped client. ``flush_config`` is not ported — the monolith has no
caller for it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..cache.workflow_import_cache import get_import_config, set_import_config
from ..config import current_settings
from ..errors import BusinessException
from ..models.app_model import App, AppType
from ..schemas.app_schema import AppCreate
from ..schemas.workflow_schema import WorkflowConfigCreate
from ..workflow.adapters.base_adapter import WorkflowImportResult
from ..workflow.adapters.errors import InvalidConfiguration, UnsupportedPlatform
from ..workflow.adapters.registry import PlatformAdapterRegistry
from .app_service import AppService
from .workflow_service import WorkflowService


class WorkflowImportService:
    def __init__(self, db: Session):
        self.db = db
        self.registry = PlatformAdapterRegistry
        self.cache_timeout = current_settings().workflow_import_cache_timeout

        self.app_service = AppService(db)
        self.workflow_service = WorkflowService(db)

    def upload_config(
            self,
            *,
            platform: str,
            config: dict[str, Any],
            redis_client: Any,
    ) -> WorkflowImportResult:
        if not self.registry.is_supported(platform):
            return WorkflowImportResult(
                success=False,
                temp_id=None,
                workflow_id=None,
                errors=[UnsupportedPlatform(platform=platform)]
            )

        adapter = self.registry.get_adapter(platform, config)

        if not adapter.validate_config():
            return WorkflowImportResult(
                success=False,
                temp_id=None,
                workflow_id=None,
                errors=[InvalidConfiguration()] + adapter.errors
            )

        workflow_config = adapter.parse_workflow()
        temp_id = uuid.uuid4().hex
        set_import_config(redis_client, temp_id, workflow_config.model_dump(), self.cache_timeout)
        return WorkflowImportResult(
            success=True,
            temp_id=temp_id,
            workflow_id=None,
            edges=workflow_config.edges,
            nodes=workflow_config.nodes,
            variables=workflow_config.variables,
            environment_variables=workflow_config.environment_variables,
            features=workflow_config.features,
            warnings=workflow_config.warnings,
            errors=workflow_config.errors
        )

    def save_workflow(
            self,
            *,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID,
            temp_id: str,
            name: str,
            description: str | None,
            redis_client: Any,
    ) -> App:
        config = get_import_config(redis_client, temp_id)
        if config is None:
            raise BusinessException("Configuration import timed out. Please try again.")
        config = json.loads(config)
        unique_name = self.app_service._unique_app_name(
            name, workspace_id, config.get("workflow_type", AppType.WORKFLOW)
        )
        app = self.app_service.create_app(
            user_id=user_id,
            workspace_id=workspace_id,
            data=AppCreate(
                name=unique_name,
                description=description,
                type=config.get("workflow_type", AppType.WORKFLOW),
                workflow_config=WorkflowConfigCreate(
                    nodes=config["nodes"],
                    edges=config["edges"],
                    variables=config["variables"],
                    environment_variables=config.get("environment_variables", []),
                    features=config.get("features", {}),
                    workflow_type=config.get("workflow_type", "workflow")
                )
            )
        )

        return app


__all__ = ["WorkflowImportService"]
