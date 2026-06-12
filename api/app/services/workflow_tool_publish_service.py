import uuid
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.models.app_model import App, AppType
from app.models.app_release_model import AppRelease
from app.models.tool_model import ToolConfig, WorkflowToolConfig, ToolStatus, ToolType
from app.models.workflow_model import WorkflowConfig
from app.schemas.tool_schema import WorkflowToolPublishPreviewSchema

logger = logging.getLogger(__name__)

class WorkflowToolPublishService:
    """工作流工具发布服务"""
    
    def __init__(self, db: Session):
        self.db = db

    def _validate_workflow_app(self, app_id: uuid.UUID, workspace_id: uuid.UUID) -> App:
        app = self.db.query(App).filter(
            App.id == app_id,
            App.workspace_id == workspace_id,
            App.is_active.is_(True)
        ).first()
        
        if not app:
            raise BusinessException(f"应用不存在或无权访问: {app_id}", BizCode.NOT_FOUND)
        
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

    def _get_release(self, app: App, release_id: Optional[uuid.UUID]) -> AppRelease:
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
        """校验工作流中不包含人工介入节点（人工介入节点不支持发布为工具）"""
        for node in WorkflowToolPublishService._get_nodes(workflow_source):
            if node.get("type") == "human-intervention":
                raise BusinessException(
                    "包含人工介入节点的工作流不支持发布为工具",
                    BizCode.INVALID_PARAMETER
                )

    @staticmethod
    def _get_nodes(workflow_source: Any) -> List[Dict[str, Any]]:
        if isinstance(workflow_source, dict):
            nodes = workflow_source.get("nodes", [])
        else:
            nodes = getattr(workflow_source, "nodes", [])
        return nodes if isinstance(nodes, list) else []

    @staticmethod
    def _get_node_config(node: Dict[str, Any]) -> Dict[str, Any]:
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
    def _extract_input_parameters(workflow_source: Any) -> List[Dict[str, Any]]:
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
    def _extract_output_schema(workflow_source: Any) -> Dict[str, Any]:
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

    def get_publish_preview(
            self,
            workflow_app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            release_id: Optional[uuid.UUID] = None,
    ) -> WorkflowToolPublishPreviewSchema:
        app = self._validate_workflow_app(workflow_app_id, workspace_id)
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
        workspace_id: uuid.UUID,
        icon: Optional[str] = None,
        timeout: int = 300,
        tags: Optional[List[str]] = None,
        release_id: Optional[uuid.UUID] = None,
    ) -> ToolConfig | None:
        app = self._validate_workflow_app(workflow_app_id, workspace_id)
        release = self._get_release(app, release_id)
        workflow_config = self._get_workflow_config(workflow_app_id)
        release_config = release.config or {}
        self._validate_no_human_intervention(release_config)

        input_parameters = self._extract_input_parameters(release_config)
        output_schema = self._extract_output_schema(release_config)

        # Check if already published
        existing_tool = self.db.query(WorkflowToolConfig).filter(
            WorkflowToolConfig.app_id == workflow_app_id
        ).first()

        if existing_tool:
            # Update existing tool
            tool_config = self.db.query(ToolConfig).filter(ToolConfig.id == existing_tool.id).first()
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
