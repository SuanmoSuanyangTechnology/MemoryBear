# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/25 14:39
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.aioRedis import aio_redis_set, aio_redis_get
from app.core.config import settings
from app.core.exceptions import BusinessException
from app.core.workflow.adapters.base_adapter import WorkflowImportResult, WorkflowParserResult
from app.core.workflow.adapters.errors import UnsupportedPlatform, InvalidConfiguration
from app.core.workflow.adapters.registry import PlatformAdapterRegistry
from app.schemas import AppCreate
from app.schemas.workflow_schema import WorkflowConfigCreate
from app.services.app_service import AppService
from app.services.workflow_service import WorkflowService


class WorkflowImportService:
    def __init__(self, db: Session):
        self.db = db
        self.registry = PlatformAdapterRegistry
        self.cache_timeout = settings.WORKFLOW_IMPORT_CACHE_TIMEOUT

        self.app_service = AppService(db)
        self.workflow_service = WorkflowService(db)

    async def flush_config(self, temp_id: str, config: WorkflowParserResult):
        config_cache = await aio_redis_get(temp_id)
        if not config_cache:
            raise BusinessException("Workflow configuration has expired. Please re-upload it.")
        await aio_redis_set(temp_id, config.model_dump_json(), expire=self.cache_timeout)

    async def upload_config(
            self,
            platform: str,
            config: dict[str, Any],
    ):

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
        await aio_redis_set(temp_id, workflow_config.model_dump(), expire=self.cache_timeout)
        return WorkflowImportResult(
            success=True,
            temp_id=temp_id,
            workflow_id=None,
            edges=workflow_config.edges,
            nodes=workflow_config.nodes,
            variables=workflow_config.variables,
            features=workflow_config.features,
            warnings=workflow_config.warnings,
            errors=workflow_config.errors
        )

    async def save_workflow(
            self,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID,
            temp_id: str,
            name: str,
            description: str | None,
    ):
        config = await aio_redis_get(temp_id)
        if config is None:
            raise BusinessException("Configuration import timed out. Please try again.")
        config = json.loads(config)
        app = self.app_service.create_app(
            user_id=user_id,
            workspace_id=workspace_id,
            data=AppCreate(
                name=name,
                description=description,
                type="workflow",
                workflow_config=WorkflowConfigCreate(
                    nodes=config["nodes"],
                    edges=config["edges"],
                    variables=config["variables"],
                    features=config.get("features", {})
                )
            )
        )
        return app
