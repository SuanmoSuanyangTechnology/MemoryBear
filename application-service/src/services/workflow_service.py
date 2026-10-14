"""
Workflow service.

Covers the config preparation needed by the definition-plane write paths
(``_prepare_workflow_config_dict``), file-variable default resolution before
workflow draft updates (``_resolve_variables_file_defaults``), and DSL export
(``export_workflow_dsl``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import yaml
from sqlalchemy.orm import Session

from ..cache.workflow_cache import delete_workflow_config
from ..config import current_settings
from ..errors import BizCode, BusinessException
from ..models.app_model import App
from ..models.file_metadata_model import FileMetadata
from ..models.workflow_model import WorkflowConfig
from ..repositories.workflow_repository import WorkflowConfigRepository
from ..schemas.app_schema import FileType
from ..utils.file_processor import build_file_object_dict_from_meta, fetch_remote_file_meta
from ..utils.workflow_triggers import TRIGGER_NODES_PREPARED_FLAG, normalize_trigger_nodes
from ..utils.workflow_validator import WorkflowValidator

logger = logging.getLogger(__name__)


class WorkflowService:
    """Workflow business logic (definition-plane write subset)."""

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = WorkflowConfigRepository(db)

    @staticmethod
    def _prepare_nodes(
        nodes: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        try:
            return normalize_trigger_nodes(nodes)
        except ValueError as exc:
            raise BusinessException(
                code=BizCode.INVALID_PARAMETER,
                message=f"工作流触发器配置无效: {exc}",
            ) from exc

    @staticmethod
    def _prepare_triggers(
        triggers: list[dict[str, Any]] | None,
        _nodes: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        return triggers or []

    def _prepare_workflow_config_dict(
        self,
        *,
        nodes: list[dict[str, Any]] | None,
        edges: list[dict[str, Any]] | None,
        variables: list[dict[str, Any]] | None,
        environment_variables: list[dict[str, Any]] | None,
        execution_config: dict[str, Any] | None,
        features: dict[str, Any] | None,
        triggers: list[dict[str, Any]] | None,
        workflow_type: str | None,
    ) -> dict[str, Any]:
        normalized_nodes = self._prepare_nodes(nodes)
        normalized_triggers = self._prepare_triggers(triggers, normalized_nodes)
        return {
            "nodes": normalized_nodes,
            "edges": edges or [],
            "variables": variables or [],
            "environment_variables": environment_variables or [],
            "execution_config": execution_config or {},
            "features": features or {},
            "triggers": normalized_triggers,
            "workflow_type": workflow_type or "workflow",
            TRIGGER_NODES_PREPARED_FLAG: True,
        }

    def _resolve_variables_file_defaults(
        self,
        variables: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert FileInput-format defaults in workflow variables to full FileObject dicts."""

        def _resolve_one(item: dict) -> dict | None:
            if not isinstance(item, dict) or item.get("is_file"):
                return item
            transfer_method = item.get("transfer_method", "remote_url")
            file_type = FileType.trans(item.get("type", "document"))
            origin_file_type = item.get("file_type") or file_type
            if transfer_method == "remote_url":
                url = item.get("url", "")
                return (
                    fetch_remote_file_meta(url, file_type, origin_file_type)
                    if url
                    else None
                )
            else:
                return self._resolve_local_file(
                    item.get("upload_file_id"), file_type, origin_file_type
                )

        result = []
        for var_def in variables:
            var_type = var_def.get("type", "")
            default = var_def.get("default")
            if var_type == "file" and isinstance(default, dict) and not default.get("is_file"):
                var_def = {**var_def, "default": _resolve_one(default)}
            elif var_type == "array[file]" and isinstance(default, list):
                resolved = []
                for item in default:
                    r = _resolve_one(item)
                    if r is not None:
                        resolved.append(r)
                var_def = {**var_def, "default": resolved}
            result.append(var_def)
        return result

    def _resolve_local_file(
        self,
        upload_file_id,
        file_type: str,
        origin_file_type: str,
    ) -> dict | None:
        """Synchronous version of resolve_local_file_object_dict (admin-plane write path)."""
        try:
            fid = uuid.UUID(str(upload_file_id))
        except ValueError:
            return None

        meta = self.db.query(FileMetadata).filter(
            FileMetadata.id == fid,
            FileMetadata.status == "completed",
        ).first()
        if not meta:
            return None

        url = f"{current_settings().file_local_server_url}/storage/permanent/{fid}"
        return build_file_object_dict_from_meta(
            file_type=file_type,
            transfer_method="local_file",
            origin_file_type=origin_file_type,
            file_id=str(fid),
            url=url,
            file_name=meta.file_name,
            file_size=meta.file_size,
            file_ext=meta.file_ext,
            content_type=meta.content_type,
        )

    def create_workflow_config(
            self,
            *,
            app_id: uuid.UUID,
            nodes: list[dict[str, Any]],
            edges: list[dict[str, Any]],
            variables: list[dict[str, Any]] | None = None,
            environment_variables: list[dict[str, Any]] | None = None,
            execution_config: dict[str, Any] | None = None,
            features: dict[str, Any] | None = None,
            triggers: list[dict[str, Any]] | None = None,
            workflow_type: str = "workflow",
            validate: bool = True,
            redis_client: Any
    ) -> WorkflowConfig:
        """Create a workflow config.

        Args:
            app_id: App ID
            nodes: Node list
            edges: Edge list
            variables: Variable list
            execution_config: Execution config
            features: Feature flags
            triggers: Trigger list
            workflow_type: Workflow type
            validate: Whether to validate the config
            redis_client: Shared-cache client for the cross-process invalidation.

        Returns:
            The workflow config

        Raises:
            BusinessException: When the config is invalid
        """
        config_dict = self._prepare_workflow_config_dict(
            nodes=nodes,
            edges=edges,
            variables=variables,
            environment_variables=environment_variables,
            execution_config=execution_config,
            features=features,
            triggers=triggers,
            workflow_type=workflow_type,
        )
        normalized_nodes = config_dict["nodes"]
        normalized_triggers = config_dict["triggers"]

        # Validate the config
        if validate:
            is_valid, errors = WorkflowValidator.validate(config_dict)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # Create or update the config
        config = self.config_repo.create_or_update(
            app_id=app_id,
            nodes=normalized_nodes,
            edges=edges,
            variables=variables,
            environment_variables=environment_variables,
            execution_config=execution_config,
            features=features,
            triggers=normalized_triggers,
            workflow_type=workflow_type
        )
        delete_workflow_config(redis_client, app_id)

        logger.info(f"创建工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def get_workflow_config(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """Get the workflow config (ORM row, for the update path)."""
        return self.config_repo.get_by_app_id(app_id)

    def export_workflow_dsl(self, app_id: uuid.UUID):
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        app: App = config.app
        dsl_info = {
            "app": {
                "name": app.name,
                "description": app.description,
                "icon": app.icon,
                "icon_type": app.icon_type
            },
            "workflow": {
                "variables": config.variables,
                "edges": config.edges,
                "nodes": config.nodes,
                "execution_config": config.execution_config,
                "triggers": config.triggers
            }
        }
        return yaml.dump(dsl_info, default_flow_style=False, allow_unicode=True)


__all__ = ["WorkflowService"]
