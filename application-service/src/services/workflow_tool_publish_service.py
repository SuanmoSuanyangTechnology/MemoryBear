"""Workflow tool publish service.

Validation order matches the monolith ``WorkflowToolPublishService``: app ->
release -> model reference gate -> workflow config, followed by
human-intervention node rejection, input/output parameter extraction, and the
same-name tool update branch. The model reference gate is reached through the
``model_ref_gate`` module (same semantics as the publish / rollback activation
points).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.app_model import App, AppType
from ..models.app_release_model import AppRelease
from ..models.tool_model import ToolConfig, ToolStatus, ToolType, WorkflowToolConfig
from ..models.workflow_model import WorkflowConfig
from ..models.workspace_model import Workspace
from ..schemas.tool_schema import WorkflowToolPublishPreviewSchema
from ..utils.db_locks import acquire_advisory_xact_lock
from ..utils.model_config_refs import config_ref_ids
from .model_ref_gate import assert_refs_publishable

logger = logging.getLogger(__name__)

TOOL_NAME_UNIQUE_INDEX = "ux_tool_configs_tenant_type_name_active"
WORKFLOW_APP_UNIQUE_INDEX = "ux_workflow_tool_configs_app_id"


class WorkflowToolPublishService:
    """Workflow tool publish service."""

    def __init__(self, db: Session):
        self.db = db

    def _validate_workflow_app(self, app_id: uuid.UUID, tenant_id: uuid.UUID) -> App:
        # Tools are tenant-scoped: the resource-center list shows tools from
        # every workspace, so republishing must not require the operator to be
        # in the source app's workspace. Ownership is enforced at the tenant.
        app = (
            self.db.query(App)
            .join(Workspace, Workspace.id == App.workspace_id)
            .filter(
                App.id == app_id,
                Workspace.tenant_id == tenant_id,
            )
            .first()
        )

        if not app:
            raise BusinessException(f"应用不存在或无权访问: {app_id}", BizCode.NOT_FOUND)

        # A deleted source app leaves the tool row visible in older installs;
        # report the real reason instead of the tool silently going stale.
        if not app.is_active:
            raise BusinessException(
                "来源应用已删除，该工具已不可用",
                BizCode.TOOL_SELECTION_INACTIVE,
                data={"reason": "SOURCE_APP_DELETED", "app_id": str(app_id)},
            )

        if app.type != AppType.PURE_WORKFLOW:
            raise BusinessException(
                f"只有 Workflow(纯工作流) 类型可以发布为工具，当前类型: {app.type}。",
                BizCode.INVALID_PARAMETER
            )
        return app

    def _get_workflow_config(self, app_id: uuid.UUID) -> WorkflowConfig:
        config = self.db.query(WorkflowConfig).filter(
            WorkflowConfig.app_id == app_id,
            WorkflowConfig.is_active.is_(True)
        ).first()

        if not config:
            raise BusinessException("工作流配置不存在", BizCode.NOT_FOUND)
        return config

    def _get_release(self, app: App, release_id: uuid.UUID | None) -> AppRelease:
        resolved_release_id = release_id or app.current_release_id
        if not resolved_release_id:
            raise BusinessException("请先发布工作流，再将其发布为工具", BizCode.CONFIG_MISSING)

        release = self.db.query(AppRelease).filter(
            AppRelease.id == resolved_release_id,
            AppRelease.app_id == app.id,
            AppRelease.is_active.is_(True),
        ).first()
        if not release:
            raise BusinessException("工作流发布版本不存在", BizCode.NOT_FOUND)
        return release

    @staticmethod
    def _validate_no_human_intervention(workflow_source: Any) -> None:
        """Validate that the workflow contains no human-intervention nodes (these
        cannot be published as tools).
        """
        for node in WorkflowToolPublishService._get_nodes(workflow_source):
            if node.get("type") == "human-intervention":
                raise BusinessException(
                    "包含人工介入节点的工作流不支持发布为工具",
                    BizCode.INVALID_PARAMETER
                )

    @staticmethod
    def _get_nodes(workflow_source: Any) -> list[dict[str, Any]]:
        if isinstance(workflow_source, dict):
            nodes = workflow_source.get("nodes", [])
        else:
            nodes = getattr(workflow_source, "nodes", [])
        return nodes if isinstance(nodes, list) else []

    @staticmethod
    def _get_node_config(node: dict[str, Any]) -> dict[str, Any]:
        config = node.get("config")
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _map_to_json_type(var_type: Any) -> str:
        type_str = str(var_type or "string").lower()
        if type_str in {"int", "integer"}:
            return "integer"
        if type_str in {"number", "float", "double"}:
            return "number"
        if type_str in {"bool", "boolean"}:
            return "boolean"
        if type_str.startswith("array[") or type_str == "array":
            return "array"
        if type_str in {"object", "json"}:
            return "object"
        if type_str in {"file", "array[file]"}:
            return "string"
        return "string"

    @staticmethod
    def _extract_input_parameters(workflow_source: Any) -> list[dict[str, Any]]:
        parameters = []
        for node in WorkflowToolPublishService._get_nodes(workflow_source):
            if node.get("type") == "start":
                node_config = WorkflowToolPublishService._get_node_config(node)
                variables = node_config.get("variables")
                if not isinstance(variables, list):
                    variables = node.get("variables", [])
                for var in variables:
                    if not isinstance(var, dict):
                        continue
                    parameters.append({
                        "name": var.get("name"),
                        "type": WorkflowToolPublishService._map_to_json_type(var.get("type")),
                        "description": var.get("description", ""),
                        "required": var.get("required", False)
                    })
                break
        return parameters

    @staticmethod
    def _extract_output_schema(workflow_source: Any) -> dict[str, Any]:
        schema = {"type": "object", "properties": {}}
        for node in WorkflowToolPublishService._get_nodes(workflow_source):
            if node.get("type") == "output":
                node_config = WorkflowToolPublishService._get_node_config(node)
                outputs = node_config.get("outputs")
                if not isinstance(outputs, list):
                    outputs = node.get("outputs", [])
                for out in outputs:
                    if not isinstance(out, dict):
                        continue
                    name = out.get("name")
                    if name:
                        schema["properties"][name] = {
                            "type": WorkflowToolPublishService._map_to_json_type(out.get("type"))
                        }
                break
        return schema

    def _check_name_duplicate(
            self,
            name: str,
            tenant_id: uuid.UUID,
            exclude_id: uuid.UUID | None = None,
    ) -> None:
        query = self.db.query(ToolConfig).filter(
            ToolConfig.name == name,
            ToolConfig.tool_type == ToolType.WORKFLOW.value,
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True),
        )
        if exclude_id:
            query = query.filter(ToolConfig.id != exclude_id)
        if query.first():
            raise BusinessException(f"工具名称 '{name}' 已存在", BizCode.DUPLICATE_NAME)

    def get_publish_preview(
            self,
            workflow_app_id: uuid.UUID,
            tenant_id: uuid.UUID,
            release_id: uuid.UUID | None = None,
    ) -> WorkflowToolPublishPreviewSchema:
        app = self._validate_workflow_app(workflow_app_id, tenant_id)
        release = self._get_release(app, release_id)
        release_config = release.config or {}
        self._validate_no_human_intervention(release_config)

        return WorkflowToolPublishPreviewSchema(
            app_id=str(workflow_app_id),
            release_id=str(release.id),
            release_version=release.version,
            release_version_name=release.version_name,
            input_parameters=self._extract_input_parameters(release_config),
            output_schema=self._extract_output_schema(release_config),
        )

    def publish_workflow_as_tool(
        self,
        workflow_app_id: uuid.UUID,
        tool_name: str,
        tool_description: str,
        tenant_id: uuid.UUID,
        icon: str | None = None,
        timeout: int = 300,
        tags: list[str] | None = None,
        release_id: uuid.UUID | None = None,
    ) -> ToolConfig | None:
        app = self._validate_workflow_app(workflow_app_id, tenant_id)
        release = self._get_release(app, release_id)
        # Effective-reference gate: a tool rebuilds the workflow from its bound
        # release_id and may bind a historical release, so the snapshot models
        # are validated for availability before binding (2026-09-20).
        assert_refs_publishable(
            self.db, config_ref_ids(release.config, release.default_model_config_id)
        )
        workflow_config = self._get_workflow_config(workflow_app_id)
        release_config = release.config or {}
        self._validate_no_human_intervention(release_config)

        input_parameters = self._extract_input_parameters(release_config)
        output_schema = self._extract_output_schema(release_config)

        # Serialize one app's publish flow: the app_id uniqueness spans two
        # tables, and the name probe must stay paired with the write.
        acquire_advisory_xact_lock(
            self.db, f"workflow_tool_publish:{workflow_app_id}"
        )

        # Check if already published
        existing_tool = self.db.query(WorkflowToolConfig).filter(
            WorkflowToolConfig.app_id == workflow_app_id
        ).first()
        self._check_name_duplicate(
            tool_name,
            tenant_id,
            exclude_id=existing_tool.id if existing_tool else None,
        )

        try:
            if existing_tool:
                # Update existing tool
                tool_config = self.db.query(ToolConfig).filter(
                    ToolConfig.id == existing_tool.id
                ).first()
                tool_config.name = tool_name
                tool_config.description = tool_description
                tool_config.is_active = True
                tool_config.icon = icon
                if tags is not None:
                    tool_config.tags = tags

                existing_tool.input_parameters = input_parameters
                existing_tool.output_schema = output_schema
                existing_tool.timeout = timeout
                existing_tool.workflow_config_id = workflow_config.id
                existing_tool.release_id = release.id

                self.db.commit()
                self.db.refresh(tool_config)
                return tool_config

            # Create new tool
            tool_id = uuid.uuid4()
            tool_config = ToolConfig(
                id=tool_id,
                name=tool_name,
                description=tool_description,
                icon=icon,
                tool_type=ToolType.WORKFLOW,
                status=ToolStatus.AVAILABLE,
                tags=tags or ["workflow"],
                tenant_id=tenant_id
            )
            self.db.add(tool_config)

            workflow_tool_config = WorkflowToolConfig(
                id=tool_id,
                app_id=workflow_app_id,
                workflow_config_id=workflow_config.id,
                release_id=release.id,
                input_parameters=input_parameters,
                output_schema=output_schema,
                timeout=timeout
            )
            self.db.add(workflow_tool_config)

            self.db.commit()
            self.db.refresh(tool_config)
            return tool_config
        except IntegrityError as e:
            self.db.rollback()
            constraint = str(getattr(e, "orig", e))
            if WORKFLOW_APP_UNIQUE_INDEX in constraint:
                raise BusinessException(
                    "该工作流已发布为工具", BizCode.DUPLICATE_NAME
                ) from e
            if TOOL_NAME_UNIQUE_INDEX in constraint:
                raise BusinessException(
                    f"工具名称 '{tool_name}' 已存在", BizCode.DUPLICATE_NAME
                ) from e
            raise
