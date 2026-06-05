"""
工作流服务层
"""
import datetime
import time
import logging
import uuid
from typing import Any, Annotated, Optional

import yaml
from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.core.workflow.node_cache import normalize_cache_value, WorkflowNodeCacheManager
from app.core.workflow.utils.secret_masker import mask_secrets
from app.core.workflow.triggers import (
    build_schedule_now_payload,
    get_trigger_type,
    is_trigger_enabled,
    iter_trigger_nodes,
    is_schedule_trigger_due,
    normalize_trigger_nodes,
    TRIGGER_NODES_PREPARED_FLAG,
)
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.workflow.adapters.registry import PlatformAdapterRegistry
from app.core.workflow.executor import execute_workflow, execute_workflow_stream
from app.core.workflow.nodes.enums import NodeType
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.core.workflow.validator import validate_workflow_config
from app.db import get_db
from app.models import App, AppRelease
from app.models.workflow_model import WorkflowConfig, WorkflowExecution, WorkflowNodeExecution
from app.repositories import knowledge_repository
from app.repositories.workflow_repository import (
    WorkflowConfigRepository,
    WorkflowExecutionRepository,
    WorkflowNodeExecutionRepository,
    WorkflowNodeCacheRepository,
)
from app.schemas import DraftRunRequest, FileInput, FileType
from app.services.annotation_service import AnnotationService
from app.services.conversation_service import ConversationService
from app.services.multi_agent_service import convert_uuids_to_str
from app.models.annotation_model import HitLogSource
from app.services.multimodal_service import MultimodalService
from app.services.workspace_service import get_workspace_storage_type_without_auth

logger = logging.getLogger(__name__)
class WorkflowService:
    """工作流服务"""

    DEBUG_STATE_NODE_ID = "__workflow_debug_state__"
    DEBUG_STATE_NODE_TYPE = "debug-state"
    DEBUG_STATE_NODE_NAME = "Workflow Debug State"
    DEBUG_STATE_SOURCE = "debug_state"

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = WorkflowConfigRepository(db)
        self.execution_repo = WorkflowExecutionRepository(db)
        self.node_execution_repo = WorkflowNodeExecutionRepository(db)
        self.node_cache_repo = WorkflowNodeCacheRepository(db)
        self.conversation_service = ConversationService(db)
        self.multimodal_service = MultimodalService(db)

        self.registry = PlatformAdapterRegistry

    @staticmethod
    def _build_runtime_workflow_config_from_release(
        release: AppRelease,
        real_config_id: uuid.UUID | None = None,
    ) -> WorkflowConfig:
        """从发布版本快照重建运行时 WorkflowConfig。"""
        cfg = release.config or {}
        now = release.created_at or utcnow_naive()
        normalized_nodes = WorkflowService._prepare_nodes(cfg.get("nodes", []))
        return WorkflowConfig(
            id=real_config_id or cfg.get("id") or uuid.uuid4(),
            app_id=release.app_id,
            nodes=normalized_nodes,
            edges=cfg.get("edges", []),
            variables=cfg.get("variables", []),
            environment_variables=cfg.get("environment_variables", []),
            execution_config=cfg.get("execution_config", {}),
            triggers=cfg.get("triggers", []),
            features=cfg.get("features", {}),
            workflow_type=cfg.get("workflow_type", "workflow"),
            is_active=True,
            created_at=now,
            updated_at=now,
        )

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

    def _build_runtime_workflow_config_dict(
        self,
        *,
        app_id: uuid.UUID,
        workflow_config: WorkflowConfig,
        features: dict[str, Any] | None = None,
        runtime_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "app_id": str(app_id),
            "workflow_config_id": str(workflow_config.id),
            **self._prepare_workflow_config_dict(
                nodes=workflow_config.nodes,
                edges=workflow_config.edges,
                variables=workflow_config.variables,
                environment_variables=workflow_config.environment_variables,
                execution_config=workflow_config.execution_config,
                features=features if features is not None else workflow_config.features,
                triggers=workflow_config.triggers,
                workflow_type=workflow_config.workflow_type,
            ),
            "runtime_options": runtime_options or {},
        }

    @staticmethod
    def _merge_trigger_context(
        input_data: dict[str, Any],
        trigger_type: str,
        trigger_id: str | None,
        trigger_meta: dict[str, Any] | None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not trigger_id and not trigger_meta and trigger_type == "manual" and not trigger_payload:
            return input_data

        merged = dict(input_data)
        merged["trigger"] = {
            "type": trigger_type,
            "id": trigger_id,
            "meta": trigger_meta or {},
        }
        if trigger_payload is not None:
            merged["trigger_payload"] = trigger_payload
        return merged

    @staticmethod
    def _find_debug_trigger_node(
        nodes: list[dict[str, Any]] | None,
        *,
        preferred_node_id: str | None = None,
    ) -> dict[str, Any] | None:
        if preferred_node_id:
            for node in nodes or []:
                if node.get("id") == preferred_node_id and node.get("type") == NodeType.TRIGGER:
                    return node

        for node in nodes or []:
            if node.get("type") == NodeType.TRIGGER:
                return node

        return None

    @staticmethod
    def _build_debug_trigger_context(
        trigger_node: dict[str, Any],
    ) -> tuple[str, str | None, dict[str, Any], dict[str, Any]]:
        trigger_type = get_trigger_type(trigger_node) or "manual"
        trigger_id = trigger_node.get("id")
        config = trigger_node.get("config") or {}
        current_time = datetime.datetime.now(datetime.timezone.utc)
        generated_at = to_iso_z(current_time)

        if trigger_type == "schedule":
            return (
                "schedule",
                trigger_id,
                {"source": "debug", "mode": "test_run", "generated_at": generated_at},
                {
                    "now": build_schedule_now_payload(current_time, config.get("timezone", "UTC")),
                    "meta": {
                        "trigger_type": "schedule",
                        "source": "debug",
                    },
                },
            )

        if trigger_type == "webhook":
            return (
                "webhook",
                trigger_id,
                {"source": "debug", "mode": "test_run", "generated_at": generated_at},
                {
                    "body": {},
                    "query": {},
                    "headers": {},
                    "meta": {
                        "trigger_type": "webhook",
                        "source": "debug",
                        "method": str(config.get("method", "POST")).upper(),
                        "route_key": config.get("route_key", ""),
                    },
                },
            )

        return (
            trigger_type or "manual",
            trigger_id,
            {"source": "debug", "mode": "test_run", "generated_at": generated_at},
            {},
        )

    def _ensure_debug_trigger_args(
        self,
        nodes: list[dict[str, Any]] | None,
        trigger_type: str,
        trigger_id: str | None,
        trigger_meta: dict[str, Any] | None,
        trigger_payload: dict[str, Any] | None,
    ) -> tuple[str, str | None, dict[str, Any] | None, dict[str, Any] | None]:
        if trigger_payload is not None:
            return trigger_type, trigger_id, trigger_meta, trigger_payload

        trigger_node = self._find_debug_trigger_node(nodes)
        if not trigger_node:
            return trigger_type, trigger_id, trigger_meta, trigger_payload

        debug_trigger_type, debug_trigger_id, debug_trigger_meta, debug_trigger_payload = (
            self._build_debug_trigger_context(trigger_node)
        )
        return (
            debug_trigger_type,
            debug_trigger_id,
            trigger_meta or debug_trigger_meta,
            debug_trigger_payload,
        )

    def _ensure_debug_trigger_input_data(
        self,
        nodes: list[dict[str, Any]] | None,
        input_data: dict[str, Any],
        *,
        preferred_node_id: str | None = None,
    ) -> dict[str, Any]:
        trigger_node = self._find_debug_trigger_node(nodes, preferred_node_id=preferred_node_id)

        if input_data.get("trigger_payload") is not None:
            # trigger_payload 已有，只补全缺失的 trigger / meta
            if trigger_node and get_trigger_type(trigger_node) == "webhook":
                cfg = trigger_node.get("config") or {}
                merged = dict(input_data)
                payload = dict(input_data["trigger_payload"])
                if not payload.get("meta"):
                    payload["meta"] = {
                        "trigger_type": "webhook",
                        "method": str(cfg.get("method", "POST")).upper(),
                        "route_key": cfg.get("route_key", ""),
                    }
                    merged["trigger_payload"] = payload
                if not merged.get("trigger"):
                    merged["trigger"] = {
                        "type": "webhook",
                        "id": trigger_node.get("id"),
                        "meta": {"source": "debug", "mode": "test_run"},
                    }
                return merged
            return input_data

        if not trigger_node:
            return input_data

        trigger_type, trigger_id, trigger_meta, trigger_payload = self._build_debug_trigger_context(trigger_node)
        merged = dict(input_data)
        merged["trigger"] = {
            "type": trigger_type,
            "id": trigger_id,
            "meta": trigger_meta,
        }
        merged["trigger_payload"] = trigger_payload
        return merged

    def update_trigger_runtime_state(
        self,
        app_id: uuid.UUID,
        trigger_id: str,
        runtime: dict[str, Any],
    ) -> WorkflowConfig | None:
        return self.config_repo.update_trigger_runtime(app_id, trigger_id, runtime)

    def update_release_trigger_runtime_state(
        self,
        release_id: uuid.UUID,
        trigger_id: str,
        runtime: dict[str, Any],
    ) -> AppRelease | None:
        release = self.db.get(AppRelease, release_id)
        if not release or not isinstance(release.config, dict):
            return None

        config = dict(release.config or {})
        nodes = list(config.get("nodes") or [])
        updated = False
        for node in nodes:
            if node.get("type") == NodeType.TRIGGER and node.get("id") == trigger_id:
                node["runtime"] = runtime
                updated = True
                break

        if not updated:
            return None

        config["nodes"] = nodes
        release.config = config
        self.db.commit()
        self.db.refresh(release)
        return release

    @staticmethod
    def _find_trigger_node(
        nodes: list[dict[str, Any]] | None,
        *,
        trigger_id: str | None = None,
        route_key: str | None = None,
        trigger_type: str | None = None,
    ) -> dict[str, Any] | None:
        for node in iter_trigger_nodes(nodes, trigger_type=trigger_type):
            config = node.get("config") or {}
            if trigger_id and node.get("id") == trigger_id:
                return node
            if route_key and config.get("route_key") == route_key:
                return node
        return None

    def find_published_webhook_trigger(
        self,
        route_key: str,
    ) -> tuple[App, AppRelease, WorkflowConfig, dict[str, Any]] | None:
        apps = self.db.query(App).filter(
            App.is_active.is_(True),
            App.current_release_id.isnot(None),
            App.type.in_(["workflow", "pure_workflow"]),
        ).all()

        for app in apps:
            release = app.current_release
            if not release or not isinstance(release.config, dict):
                continue

            config = self._build_runtime_workflow_config_from_release(
                release,
                real_config_id=(app.workflow_config.id if app.workflow_config else None),
            )
            trigger = self._find_trigger_node(
                config.nodes,
                route_key=route_key,
                trigger_type="webhook",
            )
            if trigger and is_trigger_enabled(trigger):
                return app, release, config, trigger
        return None

    def get_due_schedule_triggers(
        self,
        now: datetime.datetime | None = None,
    ) -> list[tuple[App, AppRelease, WorkflowConfig, dict[str, Any]]]:
        current_time = now or datetime.datetime.now(datetime.timezone.utc)
        due_triggers: list[tuple[App, AppRelease, WorkflowConfig, dict[str, Any]]] = []
        apps = self.db.query(App).filter(
            App.is_active.is_(True),
            App.current_release_id.isnot(None),
            App.type.in_(["workflow", "pure_workflow"]),
        ).all()

        for app in apps:
            release = app.current_release
            if not release or not isinstance(release.config, dict):
                continue

            config = self._build_runtime_workflow_config_from_release(
                release,
                real_config_id=(app.workflow_config.id if app.workflow_config else None),
            )
            for trigger in iter_trigger_nodes(config.nodes, trigger_type="schedule"):
                if not is_trigger_enabled(trigger):
                    continue
                if is_schedule_trigger_due(trigger, current_time):
                    due_triggers.append((app, release, config, trigger))

        return due_triggers

    @staticmethod
    def _build_draft_payload_from_trigger_data(
        trigger_data: dict[str, Any],
    ) -> DraftRunRequest:
        return DraftRunRequest(
            message=trigger_data.get("message"),
            variables=trigger_data.get("variables") or {},
            conversation_id=trigger_data.get("conversation_id"),
            user_id=trigger_data.get("user_id"),
            stream=False,
            files=[],
        )

    @staticmethod
    def _store_trigger_meta(
        execution: WorkflowExecution,
        trigger_id: str | None,
        trigger_meta: dict[str, Any] | None,
        external_event_id: str | None = None,
    ) -> None:
        execution.meta_data = {
            **(execution.meta_data or {}),
            "trigger_id": trigger_id,
            "trigger_meta": trigger_meta or {},
            "external_event_id": external_event_id,
        }

    async def run_with_trigger(
        self,
        *,
        app_id: uuid.UUID,
        payload: DraftRunRequest,
        config: WorkflowConfig,
        workspace_id: uuid.UUID,
        trigger_type: str,
        trigger_id: str | None = None,
        trigger_meta: dict[str, Any] | None = None,
        trigger_payload: dict[str, Any] | None = None,
        release_id: uuid.UUID | None = None,
        source: str = "",
    ) -> dict[str, Any]:
        return await self.run(
            app_id=app_id,
            payload=payload,
            config=config,
            workspace_id=workspace_id,
            release_id=release_id,
            source=source,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            trigger_meta=trigger_meta,
            trigger_payload=trigger_payload,
        )

    async def invoke_webhook_trigger(
        self,
        *,
        app: App,
        release: AppRelease,
        config: WorkflowConfig,
        trigger: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        route_key = (trigger.get("config") or {}).get("route_key", "")
        trigger_meta = {
            "source": "webhook",
            "route_key": route_key,
            "request": {
                "query": event.get("query", {}),
                "headers": event.get("headers", {}),
                "body": event.get("body"),
            },
        }
        return await self.run_with_trigger(
            app_id=app.id,
            payload=DraftRunRequest(stream=False, variables={}, files=[]),
            config=config,
            workspace_id=app.workspace_id,
            release_id=release.id,
            trigger_type="webhook",
            trigger_id=trigger.get("id"),
            trigger_meta=trigger_meta,
            trigger_payload=event,
            source=HitLogSource.EXTERNAL,
        )

    async def invoke_schedule_trigger(
        self,
        *,
        app: App,
        release: AppRelease,
        config: WorkflowConfig,
        trigger: dict[str, Any],
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        current_time = now or datetime.datetime.now(datetime.timezone.utc)
        schedule_event = {
            "now": build_schedule_now_payload(
                current_time,
                (trigger.get("config") or {}).get("timezone", "UTC"),
            ),
            "meta": {
                "trigger_type": "schedule",
                "app_id": str(app.id),
            },
        }
        payload = DraftRunRequest(stream=False, variables={}, files=[])
        result = await self.run_with_trigger(
            app_id=app.id,
            payload=payload,
            config=config,
            workspace_id=app.workspace_id,
            release_id=release.id,
            trigger_type="schedule",
            trigger_id=trigger.get("id"),
            trigger_meta={"source": "schedule", "run_at": to_iso_z(current_time)},
            trigger_payload=schedule_event,
            source="schedule",
        )
        return result

    def _check_annotation_match(self, app_id: uuid.UUID, message: str,
                              source: str = "") -> Optional[dict]:
        """检查是否命中标注

        Args:
            app_id: 应用ID
            message: 用户消息
            source: 来源（用于记录命中来源）
        """
        try:
            service = AnnotationService(self.db)
            setting = service.get_setting(app_id)
            if not setting or not setting.enabled:
                return None
            if not setting.model_config_id:
                return None

            annotations = service.repo.get_all_active_by_app(app_id)
            if not annotations:
                return None

            from app.models.models_model import ModelConfig
            from app.services.model_service import ModelApiKeyService
            model_cfg = self.db.query(ModelConfig).filter(
                ModelConfig.id == setting.model_config_id
            ).first()
            if not model_cfg:
                return None

            api_key_obj = ModelApiKeyService.get_available_api_key(self.db, setting.model_config_id)
            if not api_key_obj:
                return None

            from app.core.models.base import RedBearModelConfig
            config = RedBearModelConfig(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                base_url=api_key_obj.api_base or None,
                timeout=60,
                max_retries=3,
            )

            return service.find_best_match(
                query=message,
                annotations=annotations,
                threshold=setting.similarity_threshold,
                model_config=config,
                app_id=app_id,
                source=source,
            )
        except Exception as e:
            logger.warning(f"标注匹配检查失败: {e}")
            return None

    # ==================== 配置管理 ====================

    def create_workflow_config(
            self,
            app_id: uuid.UUID,
            nodes: list[dict[str, Any]],
            edges: list[dict[str, Any]],
            variables: list[dict[str, Any]] | None = None,
            environment_variables: list[dict[str, Any]] | None = None,
            execution_config: dict[str, Any] | None = None,
            features: dict[str, Any] | None = None,
            triggers: list[dict[str, Any]] | None = None,
            workflow_type: str = "workflow",
            validate: bool = True
    ) -> WorkflowConfig:
        """创建工作流配置

        Args:
            app_id: 应用 ID
            nodes: 节点列表
            edges: 边列表
            variables: 变量列表
            execution_config: 执行配置
            features: 功能特性
            triggers: 触发器列表
            workflow_type: 工作流类型
            validate: 是否验证配置

        Returns:
            工作流配置

        Raises:
            BusinessException: 配置无效时抛出
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

        # 验证配置
        if validate:
            is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # 创建或更新配置
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

        logger.info(f"创建工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def get_workflow_config(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """获取工作流配置

        Args:
            app_id: 应用 ID

        Returns:
            工作流配置或 None
        """
        return self.config_repo.get_by_app_id(app_id)

    def update_workflow_config(
            self,
            app_id: uuid.UUID,
            nodes: list[dict[str, Any]] | None = None,
            edges: list[dict[str, Any]] | None = None,
            variables: list[dict[str, Any]] | None = None,
            environment_variables: list[dict[str, Any]] | None = None,
            execution_config: dict[str, Any] | None = None,
            features: dict[str, Any] | None = None,
            triggers: list[dict[str, Any]] | None = None,
            workflow_type: str | None = None,
            validate: bool = True
    ) -> WorkflowConfig:
        """更新工作流配置

        Args:
            app_id: 应用 ID
            nodes: 节点列表
            edges: 边列表
            variables: 变量列表
            execution_config: 执行配置
            features: 功能特性
            triggers: 触发器列表
            workflow_type: 工作流类型
            validate: 是否验证配置

        Returns:
            工作流配置

        Raises:
            BusinessException: 配置不存在或无效时抛出
        """
        # 获取现有配置
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        # 合并配置
        config_dict = self._prepare_workflow_config_dict(
            nodes=nodes if nodes is not None else config.nodes,
            edges=edges if edges is not None else config.edges,
            variables=variables if variables is not None else config.variables,
            environment_variables=environment_variables if environment_variables is not None else config.environment_variables,
            execution_config=execution_config if execution_config is not None else config.execution_config,
            features=features if features is not None else config.features,
            triggers=triggers if triggers is not None else config.triggers,
            workflow_type=workflow_type if workflow_type is not None else config.workflow_type,
        )
        updated_nodes = config_dict["nodes"]
        updated_edges = config_dict["edges"]
        updated_variables = config_dict["variables"]
        updated_environment_variables = config_dict["environment_variables"]
        updated_execution_config = config_dict["execution_config"]
        updated_features = config_dict["features"]
        updated_triggers = config_dict["triggers"]
        updated_workflow_type = config_dict["workflow_type"]

        # 验证配置
        if validate:
            is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # 更新配置
        config = self.config_repo.create_or_update(
            app_id=app_id,
            nodes=updated_nodes,
            edges=updated_edges,
            variables=updated_variables,
            environment_variables=updated_environment_variables,
            execution_config=updated_execution_config,
            features=updated_features,
            triggers=updated_triggers,
            workflow_type=updated_workflow_type
        )

        logger.info(f"更新工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def delete_workflow_config(self, app_id: uuid.UUID) -> bool:
        """删除工作流配置

        Args:
            app_id: 应用 ID

        Returns:
            是否删除成功
        """
        config = self.get_workflow_config(app_id)
        if not config:
            return False
        config.is_active = False
        logger.info(f"删除工作流配置成功: app_id={app_id}, config_id={config.id}")
        return True

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

    def check_config(self, app_id: uuid.UUID) -> WorkflowConfig:
        """检查工作流配置的完整性

        Args:
            app_id: 应用 ID

        Raises:
            BusinessException: 配置不完整或不存在时抛出
        """

        # 1. 检查多智能体配置是否存在
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                "工作流配置不存在，无法运行",
                BizCode.CONFIG_MISSING
            )
        config_dict = self._prepare_workflow_config_dict(
            nodes=config.nodes,
            edges=config.edges,
            variables=config.variables,
            environment_variables=config.environment_variables,
            execution_config=config.execution_config,
            features=config.features,
            triggers=config.triggers,
            workflow_type=config.workflow_type,
        )
        is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
        if not is_valid:
            logger.warning(f"工作流配置验证失败: {errors}")
            raise BusinessException(
                code=BizCode.INVALID_PARAMETER,
                message=f"工作流配置无效: {'; '.join(errors)}"
            )
        config.nodes = config_dict["nodes"]
        config.triggers = config_dict["triggers"]
        return config

    def validate_workflow_config_for_publish(
            self,
            app_id: uuid.UUID
    ) -> tuple[bool, list[str]]:
        """验证工作流配置是否可以发布

        Args:
            app_id: 应用 ID

        Returns:
            (is_valid, errors): 是否有效和错误列表

        Raises:
            BusinessException: 配置不存在时抛出
        """
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        config_dict = self._prepare_workflow_config_dict(
            nodes=config.nodes,
            edges=config.edges,
            variables=config.variables,
            environment_variables=config.environment_variables,
            execution_config=config.execution_config,
            features=config.features,
            triggers=config.triggers,
            workflow_type=config.workflow_type,
        )

        return validate_workflow_config(config_dict, for_publish=True)

    # ==================== 执行管理 ====================

    def create_execution(
            self,
            workflow_config_id: uuid.UUID,
            app_id: uuid.UUID,
            trigger_type: str,
            release_id: uuid.UUID | None = None,
            triggered_by: uuid.UUID | None = None,
            conversation_id: uuid.UUID | None = None,
            input_data: dict[str, Any] | None = None
    ) -> WorkflowExecution:
        """创建工作流执行记录

        Args:
            release_id: 应用发布 ID
            workflow_config_id: 工作流配置 ID
            app_id: 应用 ID
            trigger_type: 触发类型
            triggered_by: 触发用户 ID
            conversation_id: 会话 ID
            input_data: 输入数据

        Returns:
            执行记录
        """
        # 生成执行 ID
        execution_id = f"exec_{uuid.uuid4().hex[:16]}"

        execution = WorkflowExecution(
            workflow_config_id=workflow_config_id,
            app_id=app_id,
            release_id=release_id,
            conversation_id=conversation_id,
            execution_id=execution_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            input_data=input_data or {},
            status="pending"
        )

        self.db.add(execution)
        self.db.commit()
        self.db.refresh(execution)

        logger.info(f"创建工作流执行记录: execution_id={execution_id}")
        return execution

    def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        """获取执行记录

        Args:
            execution_id: 执行 ID

        Returns:
            执行记录或 None
        """
        return self.execution_repo.get_by_execution_id(execution_id)

    @staticmethod
    def _serialize_execution_value(value: Any) -> Any:
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, datetime.datetime):
            return to_iso_z(value)
        if isinstance(value, dict):
            return {k: WorkflowService._serialize_execution_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [WorkflowService._serialize_execution_value(item) for item in value]
        return value

    @staticmethod
    def _normalize_variable_type_name(var_type: Any) -> str:
        if isinstance(var_type, VariableType):
            return var_type.value
        if isinstance(var_type, str) and var_type:
            if var_type == "secret":
                return VariableType.STRING.value
            return var_type
        return VariableType.ANY.value

    @staticmethod
    def _infer_variable_type_name(value: Any) -> str:
        try:
            return VariableType.type_map(value).value
        except Exception:
            return VariableType.ANY.value

    @classmethod
    def _build_typed_variable(cls, value: Any, var_type: Any = None) -> dict[str, Any]:
        serialized_value = cls._serialize_execution_value(value)
        type_name = cls._normalize_variable_type_name(var_type)
        if type_name == VariableType.ANY.value:
            type_name = cls._infer_variable_type_name(serialized_value)
        return {
            "type": type_name,
            "value": serialized_value,
        }

    @staticmethod
    def _is_typed_variable_payload(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and "type" in value
            and "value" in value
            and isinstance(value.get("type"), str)
            and value["type"] in {item.value for item in VariableType}
        )

    @classmethod
    def _normalize_typed_group(
            cls,
            raw_group: Any,
            *,
            type_map: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(raw_group, dict):
            return {}
        normalized: dict[str, Any] = {}
        for name, value in raw_group.items():
            if cls._is_typed_variable_payload(value):
                normalized[name] = cls._build_typed_variable(
                    value=value.get("value"),
                    var_type=value.get("type"),
                )
                continue
            normalized[name] = cls._build_typed_variable(
                value=value,
                var_type=(type_map or {}).get(name),
            )
        return normalized

    @classmethod
    def _unwrap_typed_variable(cls, value: Any) -> Any:
        if cls._is_typed_variable_payload(value):
            return cls._serialize_execution_value(value.get("value"))
        return cls._serialize_execution_value(value)

    @classmethod
    def _unwrap_typed_group(cls, raw_group: Any) -> dict[str, Any]:
        if not isinstance(raw_group, dict):
            return {}
        return {
            name: cls._unwrap_typed_variable(value)
            for name, value in raw_group.items()
        }

    @classmethod
    def _merge_typed_group(cls, base_group: Any, overlay_group: Any) -> dict[str, Any]:
        merged = dict(base_group or {}) if isinstance(base_group, dict) else {}
        if isinstance(overlay_group, dict):
            for name, value in overlay_group.items():
                merged[name] = value
        return merged

    @classmethod
    def _merge_execution_snapshots(
            cls,
            base_snapshot: dict[str, Any],
            overlay_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        merged_nodes = dict(base_snapshot.get("nodes") or {})
        for node_id, node_group in (overlay_snapshot.get("nodes") or {}).items():
            merged_nodes[node_id] = cls._merge_typed_group(merged_nodes.get(node_id), node_group)

        return {
            "system": cls._merge_typed_group(
                base_snapshot.get("system"),
                overlay_snapshot.get("system"),
            ),
            "conversation": cls._merge_typed_group(
                base_snapshot.get("conversation"),
                overlay_snapshot.get("conversation"),
            ),
            "environment": cls._merge_typed_group(
                base_snapshot.get("environment"),
                overlay_snapshot.get("environment"),
            ),
            "nodes": merged_nodes,
        }

    @staticmethod
    def _get_execution_source(execution: WorkflowExecution | None) -> str:
        if not execution:
            return "workflow_execution"
        meta_data = execution.meta_data or {}
        return meta_data.get("source", "workflow_execution")

    def _find_latest_base_execution(
            self,
            app_id: uuid.UUID,
            *,
            exclude_execution_id: str | None = None,
    ) -> WorkflowExecution | None:
        executions = self.execution_repo.get_by_app_id(app_id, limit=50, offset=0)
        for execution in executions:
            if exclude_execution_id and execution.execution_id == exclude_execution_id:
                continue
            if self._get_execution_source(execution) == "single_node_debug":
                continue
            if not isinstance(execution.output_data, dict):
                continue
            return execution
        return None

    @staticmethod
    def _coerce_variable_type(var_type: Any, value: Any = None) -> VariableType:
        if isinstance(var_type, VariableType):
            return var_type
        if isinstance(var_type, str):
            try:
                return VariableType(var_type)
            except ValueError:
                pass
        try:
            return VariableType.type_map(value)
        except Exception:
            return VariableType.ANY

    @staticmethod
    def _build_conversation_variable_type_map(variables: list[dict[str, Any]] | None) -> dict[str, str]:
        return {
            item.get("name"): item.get("type", VariableType.STRING.value)
            for item in (variables or [])
            if item.get("name")
        }

    @staticmethod
    def _build_system_variable_type_map() -> dict[str, str]:
        return {
            "conversation_index": VariableType.NUMBER.value,
            "conversation_id": VariableType.STRING.value,
            "execution_id": VariableType.STRING.value,
            "workspace_id": VariableType.STRING.value,
            "user_id": VariableType.STRING.value,
            "input_variables": VariableType.OBJECT.value,
            "files": VariableType.ARRAY_FILE.value,
            "trigger": VariableType.OBJECT.value,
            "trigger_payload": VariableType.OBJECT.value,
            "message": VariableType.STRING.value,
        }

    @classmethod
    def _build_missing_typed_variable(cls, var_type: Any) -> dict[str, Any]:
        type_name = cls._normalize_variable_type_name(var_type)
        if type_name == VariableType.FILE.value:
            default_value = None
        elif type_name == VariableType.ANY.value:
            default_value = None
        else:
            default_value = cls._serialize_execution_value(DEFAULT_VALUE(VariableType(type_name)))
        return {
            "type": type_name,
            "value": default_value,
        }

    @classmethod
    def _build_node_variable_type_maps(
            cls,
            nodes: list[dict[str, Any]] | None,
    ) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for node in nodes or []:
            node_id = node.get("id")
            if not node_id:
                continue
            if node.get("type") != NodeType.START.value:
                continue
            config = node.get("config") or {}
            type_map = {
                "message": VariableType.STRING.value,
                "execution_id": VariableType.STRING.value,
                "conversation_id": VariableType.STRING.value,
                "workspace_id": VariableType.STRING.value,
                "user_id": VariableType.STRING.value,
            }
            for item in config.get("variables") or []:
                name = item.get("name")
                if not name:
                    continue
                type_map[name] = cls._normalize_variable_type_name(item.get("type"))
            result[node_id] = type_map
        return result

    @staticmethod
    def _build_environment_variable_type_map(environment_variables: list[dict[str, Any]] | None) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in environment_variables or []:
            name = item.get("name")
            if not name:
                continue
            value_type = item.get("value_type", VariableType.STRING.value)
            result[name] = VariableType.NUMBER.value if value_type == "number" else VariableType.STRING.value
        return result

    @staticmethod
    def _build_environment_snapshot(environment_variables: list[dict[str, Any]] | None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in environment_variables or []:
            name = item.get("name")
            if not name:
                continue
            result[name] = item.get("value")
        return WorkflowService._serialize_execution_value(result)

    def _build_snapshot_type_maps(
            self,
            workflow_config: WorkflowConfig | None,
    ) -> dict[str, Any]:
        return {
            "system": self._build_system_variable_type_map(),
            "conversation": self._build_conversation_variable_type_map(
                workflow_config.variables if workflow_config else []
            ),
            "environment": self._build_environment_variable_type_map(
                workflow_config.environment_variables if workflow_config else []
            ),
            "nodes": self._build_node_variable_type_maps(
                workflow_config.nodes if workflow_config else []
            ),
        }

    def _extract_execution_snapshot_raw_groups(
            self,
            *,
            output_data: dict[str, Any],
            workflow_config: WorkflowConfig | None,
    ) -> dict[str, Any]:
        snapshot_data = output_data.get("snapshot") if isinstance(output_data, dict) else {}
        variables_data = output_data.get("variables") if isinstance(output_data, dict) else {}

        raw_system_vars = snapshot_data.get("system") if isinstance(snapshot_data, dict) else {}
        raw_conversation_vars = snapshot_data.get("conversation") if isinstance(snapshot_data, dict) else {}
        raw_environment_vars = snapshot_data.get("environment") if isinstance(snapshot_data, dict) else {}
        raw_node_vars = snapshot_data.get("nodes") if isinstance(snapshot_data, dict) else {}

        if not raw_system_vars and isinstance(variables_data, dict):
            raw_system_vars = variables_data.get("sys") or {}
        if not raw_conversation_vars and isinstance(variables_data, dict):
            raw_conversation_vars = variables_data.get("conv") or {}
        if not raw_environment_vars and isinstance(variables_data, dict):
            raw_environment_vars = variables_data.get("env") or {}
        if not raw_environment_vars:
            raw_environment_vars = self._build_environment_snapshot(
                workflow_config.environment_variables if workflow_config else []
            )

        return {
            "system": raw_system_vars or {},
            "conversation": raw_conversation_vars or {},
            "environment": raw_environment_vars or {},
            "nodes": raw_node_vars or {},
        }

    @classmethod
    def _build_typed_node_snapshot(
            cls,
            node_value: Any,
            *,
            type_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if isinstance(node_value, dict):
            return cls._normalize_typed_group(
                node_value,
                type_map=type_map,
            )
        return {
            "output": cls._build_typed_variable(
                node_value,
                type_map.get("output") if type_map else None,
            )
        }

    @classmethod
    def _build_ordered_node_snapshots(
            cls,
            *,
            node_executions: list[WorkflowNodeExecution],
            raw_node_vars: Any,
            node_type_maps: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        ordered_node_vars: dict[str, Any] = {}
        serialized_raw_node_vars = cls._serialize_execution_value(raw_node_vars or {})

        for node_execution in node_executions:
            node_id = node_execution.node_id
            if node_id in ordered_node_vars:
                continue
            node_type_map = node_type_maps.get(node_id)
            if isinstance(serialized_raw_node_vars, dict) and node_id in serialized_raw_node_vars:
                ordered_node_vars[node_id] = cls._build_typed_node_snapshot(
                    serialized_raw_node_vars[node_id],
                    type_map=node_type_map,
                )
                continue
            serialized_output = cls._serialize_execution_value(node_execution.output_data or {})
            if isinstance(serialized_output, dict) and "output" in serialized_output:
                serialized_output = serialized_output.get("output")
            ordered_node_vars[node_id] = cls._build_typed_node_snapshot(
                serialized_output,
                type_map=node_type_map,
            )

        if isinstance(serialized_raw_node_vars, dict):
            for node_id, node_value in serialized_raw_node_vars.items():
                if node_id not in ordered_node_vars:
                    ordered_node_vars[node_id] = cls._build_typed_node_snapshot(
                        node_value,
                        type_map=node_type_maps.get(node_id),
                    )

        for node_id, type_map in node_type_maps.items():
            node_vars = ordered_node_vars.setdefault(node_id, {})
            for var_name, var_type in type_map.items():
                if var_name not in node_vars:
                    node_vars[var_name] = cls._build_missing_typed_variable(var_type)

        return ordered_node_vars

    def _build_execution_snapshot(
            self,
            *,
            execution: WorkflowExecution,
            node_executions: list[WorkflowNodeExecution],
            output_data: dict[str, Any],
    ) -> dict[str, Any]:
        workflow_config = execution.workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
        type_maps = self._build_snapshot_type_maps(workflow_config)
        raw_groups = self._extract_execution_snapshot_raw_groups(
            output_data=output_data,
            workflow_config=workflow_config,
        )
        ordered_node_vars = self._build_ordered_node_snapshots(
            node_executions=node_executions,
            raw_node_vars=raw_groups["nodes"],
            node_type_maps=type_maps["nodes"],
        )
        return {
            "system": self._normalize_typed_group(
                raw_groups["system"],
                type_map=type_maps["system"],
            ),
            "conversation": self._normalize_typed_group(
                raw_groups["conversation"],
                type_map=type_maps["conversation"],
            ),
            "environment": self._normalize_typed_group(
                raw_groups["environment"],
                type_map=type_maps["environment"],
            ),
            "nodes": ordered_node_vars,
        }

    def _build_public_execution_snapshot_record(
            self,
            *,
            execution: WorkflowExecution,
            node_executions: list[WorkflowNodeExecution],
            output_data: dict[str, Any],
    ) -> dict[str, Any]:
        workflow_config = execution.workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
        type_maps = self._build_snapshot_type_maps(workflow_config)
        raw_groups = self._extract_execution_snapshot_raw_groups(
            output_data=output_data,
            workflow_config=workflow_config,
        )
        ordered_node_vars = self._build_ordered_node_snapshots(
            node_executions=node_executions,
            raw_node_vars=raw_groups["nodes"],
            node_type_maps=type_maps["nodes"],
        )
        return {
            "conversation": self._normalize_typed_group(
                raw_groups["conversation"],
                type_map=type_maps["conversation"],
            ),
            "nodes": ordered_node_vars,
        }

    def _build_execution_snapshot_from_record(self, execution: WorkflowExecution) -> dict[str, Any]:
        node_executions = self.node_execution_repo.get_by_execution_id(execution.id)
        output_data = self._serialize_execution_value(execution.output_data or {})
        return self._build_execution_snapshot(
            execution=execution,
            node_executions=node_executions,
            output_data=output_data if isinstance(output_data, dict) else {},
        )

    def _build_public_execution_snapshot_from_record(self, execution: WorkflowExecution) -> dict[str, Any]:
        node_executions = self.node_execution_repo.get_by_execution_id(execution.id)
        output_data = self._serialize_execution_value(execution.output_data or {})
        return self._build_public_execution_snapshot_record(
            execution=execution,
            node_executions=node_executions,
            output_data=output_data if isinstance(output_data, dict) else {},
        )

    @staticmethod
    def _normalize_public_debug_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return {
                "conversation": {},
                "nodes": {},
            }
        return {
            "conversation": snapshot.get("conversation") or {},
            "nodes": snapshot.get("nodes") or {},
        }

    def _build_reset_conversation_snapshot(
            self,
            workflow_config: WorkflowConfig | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in (workflow_config.variables if workflow_config else []) or []:
            name = item.get("name")
            if not name:
                continue
            if item.get("default") is not None:
                result[name] = self._build_typed_variable(
                    item.get("default"),
                    item.get("type"),
                )
            else:
                result[name] = self._build_typed_variable(
                    None,
                    item.get("type"),
                )
        return result

    def _build_default_debug_state_snapshot(
            self,
            workflow_config: WorkflowConfig | None,
    ) -> dict[str, Any]:
        return self._normalize_public_debug_snapshot(
            {
                "conversation": self._build_reset_conversation_snapshot(workflow_config),
                "nodes": {},
            }
        )

    @classmethod
    def _build_public_node_snapshot_from_cache_result(
            cls,
            result_data: dict[str, Any] | None,
            *,
            type_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        serialized_result = cls._serialize_execution_value(result_data or {})
        if isinstance(serialized_result, dict) and "output" in serialized_result:
            serialized_result = serialized_result.get("output")
        return cls._build_typed_node_snapshot(
            serialized_result,
            type_map=type_map,
        )

    @staticmethod
    def _extract_snapshot_node_order(snapshot: dict[str, Any] | None) -> list[str]:
        if not isinstance(snapshot, dict):
            return []
        nodes = snapshot.get("nodes")
        if not isinstance(nodes, dict):
            return []
        return [node_id for node_id in nodes.keys() if isinstance(node_id, str)]

    @classmethod
    def _apply_snapshot_node_order(
            cls,
            snapshot: dict[str, Any] | None,
            node_order: list[str] | None,
    ) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return snapshot or {}
        nodes = snapshot.get("nodes")
        if not isinstance(nodes, dict):
            return snapshot
        ordered_nodes: dict[str, Any] = {}
        seen_node_ids: set[str] = set()
        for node_id in node_order or []:
            if node_id in nodes:
                ordered_nodes[node_id] = nodes[node_id]
                seen_node_ids.add(node_id)
        for node_id, node_value in nodes.items():
            if node_id not in seen_node_ids:
                ordered_nodes[node_id] = node_value
        result = dict(snapshot)
        result["nodes"] = ordered_nodes
        return result

    def _normalize_debug_state_snapshot(
            self,
            *,
            snapshot: dict[str, Any] | None,
            node_order: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        normalized_snapshot = self._normalize_public_debug_snapshot(snapshot)
        resolved_node_order = node_order if isinstance(node_order, list) else self._extract_snapshot_node_order(
            normalized_snapshot
        )
        return self._apply_snapshot_node_order(normalized_snapshot, resolved_node_order), resolved_node_order

    @classmethod
    def _normalize_debug_state_messages(
            cls,
            messages: Any,
    ) -> list[dict[str, Any]]:
        serialized_messages = cls._serialize_execution_value(messages or [])
        return serialized_messages if isinstance(serialized_messages, list) else []

    def _build_debug_state_persist_payload(
            self,
            *,
            snapshot: dict[str, Any] | None,
            messages: Any = None,
            execution_id: str | None = None,
            source: str | None = None,
            node_order: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_snapshot, resolved_node_order = self._normalize_debug_state_snapshot(
            snapshot=snapshot,
            node_order=node_order,
        )
        normalized_messages = self._normalize_debug_state_messages(messages)
        normalized_source = source or self.DEBUG_STATE_SOURCE
        return {
            "snapshot": normalized_snapshot,
            "messages": normalized_messages,
            "node_order": resolved_node_order,
            "execution_id": execution_id,
            "source": normalized_source,
            "result_data": {
                "snapshot": normalized_snapshot,
                "messages": normalized_messages,
                "node_order": resolved_node_order,
                "execution_id": execution_id,
                "source": normalized_source,
            },
            "meta_data": {
                "messages_count": len(normalized_messages),
                "node_order": resolved_node_order,
                "execution_id": execution_id,
                "source": normalized_source,
            },
        }

    def _build_debug_state_cache_manager(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig | None,
    ) -> WorkflowNodeCacheManager:
        return self._build_node_cache_manager(
            app_id=app_id,
            workflow_config_id=workflow_config.id if workflow_config else None,
            node_id=self.DEBUG_STATE_NODE_ID,
            node_type=self.DEBUG_STATE_NODE_TYPE,
            node_name=self.DEBUG_STATE_NODE_NAME,
        )

    def _read_workflow_debug_state(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig | None,
    ) -> dict[str, Any]:
        manager = self._build_debug_state_cache_manager(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        state_cache = manager.get_latest_cache(include_inactive=False)
        result_data = state_cache.get("result_data") if state_cache else {}
        snapshot = (result_data or {}).get("snapshot") if isinstance(result_data, dict) else None
        if not isinstance(snapshot, dict):
            snapshot = self._build_default_debug_state_snapshot(workflow_config)
        snapshot, node_order = self._normalize_debug_state_snapshot(
            snapshot=snapshot,
            node_order=result_data.get("node_order") if isinstance(result_data, dict) else None,
        )
        messages = self._normalize_debug_state_messages(
            result_data.get("messages") if isinstance(result_data, dict) else None
        )
        return {
            "cache": state_cache or {},
            "snapshot": snapshot,
            "messages": messages,
            "execution_id": result_data.get("execution_id") if isinstance(result_data, dict) else None,
            "source": result_data.get("source") if isinstance(result_data, dict) else None,
            "node_order": node_order,
        }

    @classmethod
    def _compose_runtime_snapshot(
            cls,
            *,
            base_snapshot: dict[str, Any] | None,
            debug_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        base_snapshot = base_snapshot if isinstance(base_snapshot, dict) else {}
        debug_snapshot = cls._normalize_public_debug_snapshot(debug_snapshot)
        return {
            "system": dict(base_snapshot.get("system") or {}),
            "conversation": dict(debug_snapshot.get("conversation") or {}),
            "environment": dict(base_snapshot.get("environment") or {}),
            "nodes": dict(debug_snapshot.get("nodes") or {}),
        }

    @classmethod
    def _build_runtime_node_outputs(
            cls,
            *,
            base_node_outputs: dict[str, Any] | None,
            debug_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        base_node_outputs = base_node_outputs if isinstance(base_node_outputs, dict) else {}
        debug_nodes = (debug_snapshot or {}).get("nodes") if isinstance(debug_snapshot, dict) else {}
        if not isinstance(debug_nodes, dict):
            return result
        for node_id, node_group in debug_nodes.items():
            base_item = base_node_outputs.get(node_id)
            merged_item = dict(base_item) if isinstance(base_item, dict) else {}
            merged_item["output"] = cls._unwrap_typed_group(node_group)
            result[node_id] = merged_item
        return result

    def _write_workflow_debug_state(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig | None,
            snapshot: dict[str, Any],
            messages: Any = None,
            execution_id: str | None = None,
            source: str | None = None,
    ) -> dict[str, Any]:
        persist_payload = self._build_debug_state_persist_payload(
            snapshot=snapshot,
            messages=messages,
            execution_id=execution_id,
            source=source,
        )
        manager = self._build_debug_state_cache_manager(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        saved_cache = manager.save_cache(
            cache_key=manager.build_cache_key({"kind": self.DEBUG_STATE_SOURCE}),
            input_data={"kind": self.DEBUG_STATE_SOURCE},
            result_data=persist_payload["result_data"],
            source=self.DEBUG_STATE_SOURCE,
            ttl_seconds=None,
            meta_data=persist_payload["meta_data"],
        )
        return {
            "cache": saved_cache or {},
            "snapshot": persist_payload["snapshot"],
            "messages": persist_payload["messages"],
            "execution_id": persist_payload["execution_id"],
            "source": persist_payload["source"],
            "node_order": persist_payload["node_order"],
        }

    def _sync_workflow_debug_state_node(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig | None,
            node_id: str,
            node_snapshot: dict[str, Any] | None = None,
            source: str | None = None,
            execution_id: str | None = None,
    ) -> dict[str, Any]:
        debug_state = self._read_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        debug_snapshot = dict(debug_state["snapshot"])
        nodes_snapshot = dict(debug_snapshot.get("nodes") or {})
        if node_snapshot is None:
            nodes_snapshot.pop(node_id, None)
        else:
            nodes_snapshot[node_id] = node_snapshot
        debug_snapshot["nodes"] = nodes_snapshot
        return self._write_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
            snapshot=debug_snapshot,
            messages=debug_state.get("messages"),
            execution_id=execution_id if execution_id is not None else debug_state.get("execution_id"),
            source=source,
        )

    def _refresh_workflow_debug_state_from_execution(self, execution: WorkflowExecution) -> None:
        workflow_config = execution.workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
        snapshot = self._build_public_execution_snapshot_from_record(execution)
        self._write_workflow_debug_state(
            app_id=execution.app_id,
            workflow_config=workflow_config,
            snapshot=snapshot or self._build_default_debug_state_snapshot(workflow_config),
            messages=self._extract_execution_messages(execution),
            execution_id=execution.execution_id,
            source=self._get_execution_source(execution),
        )

    def _extract_execution_messages(self, execution: WorkflowExecution | None) -> list[dict[str, Any]]:
        if not execution or not isinstance(execution.output_data, dict):
            return []
        messages = self._serialize_execution_value(execution.output_data.get("messages") or [])
        return messages if isinstance(messages, list) else []

    def _extract_execution_node_outputs(self, execution: WorkflowExecution | None) -> dict[str, Any]:
        if not execution or not isinstance(execution.output_data, dict):
            return {}
        node_outputs = self._serialize_execution_value(execution.output_data.get("node_outputs") or {})
        return node_outputs if isinstance(node_outputs, dict) else {}

    def _extract_execution_runtime_state(
            self,
            execution: WorkflowExecution | None,
    ) -> dict[str, Any]:
        return {
            "execution": execution,
            "snapshot": self._build_execution_snapshot_from_record(execution) if execution else None,
            "messages": self._extract_execution_messages(execution),
            "node_outputs": self._extract_execution_node_outputs(execution),
        }

    def _build_single_node_runtime_seed(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig | None,
    ) -> dict[str, Any]:
        base_state = self._extract_execution_runtime_state(
            self._find_latest_base_execution(app_id)
        )
        debug_state = self._read_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        debug_snapshot = debug_state["snapshot"]
        return {
            "base_execution": base_state["execution"],
            "debug_state": debug_state,
            "debug_snapshot": debug_snapshot,
            "runtime_snapshot": self._compose_runtime_snapshot(
                base_snapshot=base_state["snapshot"],
                debug_snapshot=debug_snapshot,
            ),
            "messages": debug_state.get("messages") or base_state["messages"],
            "node_outputs": self._build_runtime_node_outputs(
                base_node_outputs=base_state["node_outputs"],
                debug_snapshot=debug_snapshot,
            ),
        }

    async def _prepare_single_node_start_input(
            self,
            *,
            node_config: dict[str, Any],
            input_data: dict[str, Any],
    ) -> dict[str, Any]:
        if node_config.get("type") != NodeType.START:
            return input_data
        start_node_vars = node_config.get("config", {}).get("variables", [])
        start_node_id = node_config.get("id")
        inputs = input_data.get("inputs") or {}
        variables = dict(input_data.get("variables") or {})
        for var_def in start_node_vars:
            var_name = var_def.get("name")
            keyed_value = inputs.get(f"{start_node_id}.{var_name}")
            bare_value = inputs.get(var_name)
            value = keyed_value if keyed_value is not None else bare_value
            if value is not None:
                variables[var_name] = value
        input_data["variables"] = await self._resolve_start_node_file_variables(
            start_node_vars,
            variables,
        )
        return input_data

    async def _prepare_single_node_input_files(
            self,
            input_data: dict[str, Any],
    ) -> dict[str, Any]:
        raw_files = input_data.get("files") or []
        if not raw_files:
            return input_data
        from app.schemas.app_schema import FileInput
        file_inputs = [
            FileInput(**f) if isinstance(f, dict) else f
            for f in raw_files
        ]
        input_data["files"] = await self._handle_file_input(file_inputs)
        return input_data

    @staticmethod
    def _build_single_node_cycle_nodes(workflow_config_dict: dict[str, Any]) -> list[str]:
        return [
            node.get("id") for node in workflow_config_dict.get("nodes", [])
            if node.get("type") in [NodeType.LOOP, NodeType.ITERATION]
        ]

    def _build_single_node_workflow_state(
            self,
            *,
            workflow_state_cls,
            node_id: str,
            execution_id: str,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any],
            workflow_config_dict: dict[str, Any],
            runtime_seed: dict[str, Any],
            storage_type: str | None,
            user_rag_memory_id: str | None,
    ):
        cycle_nodes = self._build_single_node_cycle_nodes(workflow_config_dict)
        return workflow_state_cls(
            messages=input_data.get("conv_messages") or runtime_seed["messages"],
            node_outputs=runtime_seed["node_outputs"],
            execution_id=execution_id,
            workspace_id=str(workspace_id),
            user_id=input_data.get("user_id", ""),
            error=None,
            error_node=None,
            cycle_nodes=cycle_nodes,
            looping=0,
            activate={node_id: True},
            memory_storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )

    @staticmethod
    def _build_single_node_execution_id() -> str:
        return f"node_{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _build_execution_context(
            execution_context_cls,
            *,
            execution_id: str,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any],
            storage_type: str | None,
            user_rag_memory_id: str | None,
    ):
        return execution_context_cls.create(
            execution_id=execution_id,
            workspace_id=str(workspace_id),
            user_id=input_data.get("user_id", ""),
            conversation_id=input_data.get("conversation_id", ""),
            memory_storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )

    async def _inject_single_node_flat_inputs(
            self,
            *,
            variable_pool,
            input_data: dict[str, Any],
    ) -> None:
        for key, value in (input_data.get("inputs") or {}).items():
            if "." not in key:
                continue
            ref_node_id, var_name = key.split(".", 1)
            var_type = VariableType.type_map(value)
            await variable_pool.new(ref_node_id, var_name, value, var_type, mut=False)

    async def _restore_snapshot_group_to_variable_pool(
            self,
            *,
            variable_pool,
            namespace: str,
            raw_group: Any,
            mut: bool,
            skip_keys: set[str] | None = None,
    ) -> None:
        if not isinstance(raw_group, dict):
            return
        for name, typed_value in raw_group.items():
            if skip_keys and name in skip_keys:
                continue
            resolved_type = self._coerce_variable_type(
                typed_value.get("type") if self._is_typed_variable_payload(typed_value) else None,
                self._unwrap_typed_variable(typed_value),
            )
            resolved_value = self._unwrap_typed_variable(typed_value)
            if resolved_value is None:
                if resolved_type == VariableType.FILE:
                    continue
                if resolved_type != VariableType.ANY:
                    resolved_value = DEFAULT_VALUE(resolved_type)
            await variable_pool.new(
                namespace=namespace,
                key=name,
                value=resolved_value,
                var_type=resolved_type,
                mut=mut,
            )

    async def _restore_variable_pool_from_snapshot(
            self,
            *,
            variable_pool,
            snapshot: dict[str, Any] | None,
    ) -> None:
        if not isinstance(snapshot, dict):
            return
        await self._restore_snapshot_group_to_variable_pool(
            variable_pool=variable_pool,
            namespace="sys",
            raw_group=snapshot.get("system"),
            mut=False,
            skip_keys={"execution_id", "workspace_id", "user_id"},
        )
        await self._restore_snapshot_group_to_variable_pool(
            variable_pool=variable_pool,
            namespace="conv",
            raw_group=snapshot.get("conversation"),
            mut=True,
        )
        await self._restore_snapshot_group_to_variable_pool(
            variable_pool=variable_pool,
            namespace="env",
            raw_group=snapshot.get("environment"),
            mut=False,
        )
        for node_id, group in (snapshot.get("nodes") or {}).items():
            await self._restore_snapshot_group_to_variable_pool(
                variable_pool=variable_pool,
                namespace=node_id,
                raw_group=group,
                mut=False,
            )

    async def _apply_input_data_overrides_to_variable_pool(
            self,
            *,
            variable_pool,
            input_data: dict[str, Any],
    ) -> None:
        if input_data.get("message") is not None:
            await variable_pool.new("sys", "message", input_data.get("message"), VariableType.STRING, mut=False)
        if input_data.get("files") is not None:
            await variable_pool.new("sys", "files", input_data.get("files"), VariableType.ARRAY_FILE, mut=False)
        if input_data.get("trigger") is not None:
            await variable_pool.new("sys", "trigger", input_data.get("trigger"), VariableType.OBJECT, mut=False)
        if input_data.get("trigger_payload") is not None:
            await variable_pool.new(
                "sys",
                "trigger_payload",
                input_data.get("trigger_payload"),
                VariableType.OBJECT,
                mut=False,
            )
        if input_data.get("conversation_id"):
            await variable_pool.new(
                "sys",
                "conversation_id",
                input_data.get("conversation_id"),
                VariableType.STRING,
                mut=False,
            )
        if input_data.get("variables") is not None:
            await variable_pool.new(
                "sys",
                "input_variables",
                input_data.get("variables") or {},
                VariableType.OBJECT,
                mut=False,
            )
        for key, value in (input_data.get("conv") or {}).items():
            await variable_pool.new(
                "conv",
                key,
                value,
                self._coerce_variable_type(None, value),
                mut=True,
            )

    @staticmethod
    def _mask_runtime_secrets(payload: dict[str, Any], variable_pool) -> dict[str, Any]:
        if not variable_pool:
            return payload
        return mask_secrets(payload, variable_pool.get_secret_values())

    @staticmethod
    def _extract_secret_values_from_environment_variables(
            environment_variables: list[dict[str, Any]] | None
    ) -> list[str]:
        secret_values: list[str] = []
        for item in environment_variables or []:
            if item.get("value_type") != "secret":
                continue
            value = item.get("value")
            if value not in (None, "", "__SECRET__"):
                secret_values.append(str(value))
        return sorted(set(secret_values), key=len, reverse=True)

    @staticmethod
    def _mask_payload_with_secret_values(payload: dict[str, Any], secret_values: list[str]) -> dict[str, Any]:
        if not secret_values:
            return payload
        return mask_secrets(payload, secret_values)

    @staticmethod
    def _build_debug_execution_output(node_id: str, status: str) -> dict[str, Any]:
        return {
            "debug": True,
            "source": "single_node_debug",
            "node_id": node_id,
            "status": status,
        }

    @staticmethod
    def _normalize_single_node_payload(node_type: str, node_name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_type": node_type,
            "node_name": node_name,
            "status": payload.get("status", "completed"),
            "input": payload.get("inputs", payload.get("input")),
            "output": payload.get("outputs", payload.get("output")),
            "process": payload.get("process"),
            "agent_log": payload.get("agent_log"),
            "token_usage": payload.get("token_usage"),
            "elapsed_time": payload.get("elapsed_time"),
            "error": payload.get("error"),
            "execution_order": 1,
            "retry_count": 0,
        }

    @staticmethod
    def _build_single_node_payload_from_node_output(
            node_id: str,
            node_type: str | None,
            node_output: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": node_output.get("status", "completed"),
            "node_id": node_id,
            "node_type": node_type,
            "inputs": node_output.get("input"),
            "outputs": node_output.get("output"),
            "process": node_output.get("process"),
            "agent_log": node_output.get("agent_log"),
            "token_usage": node_output.get("token_usage"),
            "elapsed_time": node_output.get("elapsed_time"),
            "error": node_output.get("error"),
            "cache_hit": bool(node_output.get("cache_hit", False)),
            "cache_key": node_output.get("cache_key"),
        }

    @staticmethod
    def _attach_debug_execution_id(payload: dict[str, Any], execution_id: str) -> dict[str, Any]:
        return {
            **(payload or {}),
            "execution_id": execution_id,
        }

    @staticmethod
    def _node_output_sort_key(item: tuple[str, Any]) -> int:
        node_data = item[1]
        if not isinstance(node_data, dict):
            return 0
        return int(node_data.get("execution_order", 0) or 0)

    @staticmethod
    def _safe_execution_order(value: Any, fallback: int) -> int:
        try:
            order = int(value or fallback)
        except (TypeError, ValueError):
            return fallback
        return order if 0 < order <= 2_147_483_647 else fallback

    @staticmethod
    def _get_node_name_from_config(config: WorkflowConfig | None, node_id: str) -> str | None:
        if not config:
            return None
        for node in config.nodes or []:
            if node.get("id") == node_id:
                return node.get("name")
        return None

    def _build_node_execution_record(
            self,
            *,
            execution: WorkflowExecution,
            node_id: str,
            node_data: dict[str, Any],
            source: str,
            fallback_execution_order: int,
            fallback_node_name: str | None = None,
    ) -> dict[str, Any]:
        normalized = dict(node_data or {})
        now = utcnow_naive()
        completed_at = execution.completed_at or now
        elapsed_time = normalized.pop("elapsed_time", None)
        started_at = execution.started_at or now
        if isinstance(elapsed_time, (int, float)) and elapsed_time and execution.completed_at:
            # Workflow node elapsed_time is typically in seconds; single-node debug uses milliseconds.
            if source == "single_node_debug":
                started_at = completed_at - datetime.timedelta(milliseconds=elapsed_time)
            else:
                started_at = completed_at - datetime.timedelta(seconds=elapsed_time)

        process_data = normalized.pop("process", None)
        agent_log = normalized.pop("agent_log", None)
        return {
            "execution_id": execution.id,
            "app_id": execution.app_id,
            "workflow_config_id": execution.workflow_config_id,
            "node_id": node_id,
            "node_type": normalized.pop("node_type", "unknown"),
            "node_name": normalized.pop("node_name", fallback_node_name),
            "execution_order": self._safe_execution_order(
                normalized.pop("execution_order", fallback_execution_order),
                fallback_execution_order,
            ),
            "retry_count": int(normalized.pop("retry_count", 0) or 0),
            "input_data": normalized.pop("input", None),
            "output_data": normalized.pop("output", normalized or None),
            "status": normalized.pop("status", "completed"),
            "error_message": normalized.pop("error", None),
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_time": elapsed_time,
            "token_usage": normalized.pop("token_usage", None),
            "cache_hit": bool(normalized.pop("cache_hit", False)),
            "cache_key": normalized.pop("cache_key", None),
            "meta_data": {
                "source": source,
                "process_data": process_data,
                "agent_log": agent_log,
                "workflow_config_id": str(execution.workflow_config_id),
                "conversation_id": str(execution.conversation_id) if execution.conversation_id else None,
                "debug": source == "single_node_debug",
            }
        }

    def _persist_workflow_node_executions(
            self,
            execution: WorkflowExecution,
            workflow_config: WorkflowConfig,
            result: dict[str, Any],
    ) -> None:
        node_outputs = result.get("node_outputs") or {}
        if not isinstance(node_outputs, dict) or not node_outputs:
            return

        self.node_execution_repo.delete_by_execution_id(execution.id)
        items: list[dict[str, Any]] = []
        ordered_node_outputs = sorted(node_outputs.items(), key=self._node_output_sort_key)
        for index, (node_id, node_data) in enumerate(ordered_node_outputs, start=1):
            if not isinstance(node_data, dict):
                continue
            items.append(
                self._build_node_execution_record(
                    execution=execution,
                    node_id=node_id,
                    node_data=node_data,
                    source="workflow_execution",
                    fallback_execution_order=index,
                    fallback_node_name=self._get_node_name_from_config(workflow_config, node_id),
                )
            )
        self.node_execution_repo.bulk_create(items)
        self.db.commit()

    @staticmethod
    def _build_single_node_run_id() -> str:
        return f"node_{uuid.uuid4().hex[:16]}"

    def _persist_single_node_execution(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig,
            node_id: str,
            node_type: str,
            node_name: str | None,
            payload: dict[str, Any],
            run_id: str,
            debug_input_data: dict[str, Any] | None = None,
    ) -> WorkflowNodeExecution:
        normalized_payload = self._normalize_single_node_payload(node_type, node_name, payload)
        now = utcnow_naive()
        elapsed_time = normalized_payload.get("elapsed_time")
        completed_at = now
        started_at = now
        if isinstance(elapsed_time, (int, float)) and elapsed_time:
            started_at = completed_at - datetime.timedelta(milliseconds=elapsed_time)
        process_data = normalized_payload.get("process")
        node_execution = self.node_execution_repo.create(
            execution_id=None,
            app_id=app_id,
            workflow_config_id=workflow_config.id,
            node_id=node_id,
            node_type=node_type,
            node_name=node_name,
            execution_order=1,
            retry_count=0,
            input_data=normalized_payload.get("input"),
            output_data=normalized_payload.get("output"),
            status=normalized_payload.get("status", "completed"),
            error_message=normalized_payload.get("error"),
            started_at=started_at,
            completed_at=completed_at,
            elapsed_time=elapsed_time,
            token_usage=normalized_payload.get("token_usage"),
            cache_hit=bool(normalized_payload.get("cache_hit", False)),
            cache_key=normalized_payload.get("cache_key"),
            meta_data={
                "source": "single_node_debug",
                "process_data": process_data,
                "workflow_config_id": str(workflow_config.id),
                "conversation_id": payload.get("conversation_id"),
                "debug": True,
                "run_id": run_id,
                "debug_input": normalize_cache_value(debug_input_data or {}),
            },
        )
        self.db.commit()
        self.db.refresh(node_execution)
        return node_execution

    def _build_rerun_input_from_latest_state(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig,
            node_type: str | None,
            debug_input_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        raw_input = dict(debug_input_data or {}) if isinstance(debug_input_data, dict) else {}
        latest_execution = self._find_latest_base_execution(app_id)
        latest_execution_input = (
            dict(latest_execution.input_data or {})
            if latest_execution and isinstance(latest_execution.input_data, dict)
            else {}
        )
        latest_messages = self._extract_execution_messages(latest_execution)
        debug_state = self._read_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        debug_snapshot = debug_state["snapshot"] if isinstance(debug_state, dict) else {}
        debug_source = debug_state.get("source") if isinstance(debug_state, dict) else None
        debug_messages = debug_state.get("messages") if isinstance(debug_state, dict) else []
        prefer_single_node_debug_input = debug_source == "single_node_debug"
        latest_conv = self._unwrap_typed_group(
            debug_snapshot.get("conversation") if isinstance(debug_snapshot, dict) else {}
        )

        rerun_input = {
            "message": (
                raw_input.get("message")
                if prefer_single_node_debug_input
                else latest_execution_input.get("message", raw_input.get("message"))
            ),
            "files": (
                raw_input.get("files")
                if prefer_single_node_debug_input
                else latest_execution_input.get("files", raw_input.get("files"))
            ),
            "user_id": (
                raw_input.get("user_id")
                if prefer_single_node_debug_input
                else latest_execution_input.get("user_id", raw_input.get("user_id"))
            ),
            "trigger": (
                raw_input.get("trigger")
                if prefer_single_node_debug_input
                else latest_execution_input.get("trigger", raw_input.get("trigger"))
            ),
            "trigger_payload": (
                raw_input.get("trigger_payload")
                if prefer_single_node_debug_input
                else latest_execution_input.get("trigger_payload", raw_input.get("trigger_payload"))
            ),
            "conversation_id": (
                raw_input.get("conversation_id", "")
                if prefer_single_node_debug_input
                else latest_execution_input.get("conversation_id") or raw_input.get("conversation_id", "")
            ),
            "conv_messages": (
                debug_messages or raw_input.get("conv_messages") or latest_messages or []
                if prefer_single_node_debug_input
                else debug_messages or latest_messages or raw_input.get("conv_messages") or []
            ),
            "variables": dict(
                raw_input.get("variables") or latest_execution_input.get("variables") or {}
                if prefer_single_node_debug_input
                else latest_execution_input.get("variables") or raw_input.get("variables") or {}
            ),
            "conv": (
                dict(raw_input.get("conv") or latest_conv or {})
                if prefer_single_node_debug_input
                else latest_conv or dict(raw_input.get("conv") or {})
            ),
        }
        if node_type == NodeType.START:
            rerun_input["inputs"] = dict(raw_input.get("inputs") or {})
        else:
            rerun_input["inputs"] = {}
        return normalize_cache_value(rerun_input)

    def _build_public_conversation_snapshot_from_variable_pool(
            self,
            *,
            workflow_config: WorkflowConfig | None,
            variable_pool,
    ) -> dict[str, Any]:
        if not variable_pool:
            return {}
        type_map = self._build_snapshot_type_maps(workflow_config)["conversation"]
        return self._normalize_typed_group(
            variable_pool.get_all_conversation_vars(),
            type_map=type_map,
        )

    def _refresh_workflow_debug_state_from_single_node_payload(
            self,
            *,
            app_id: uuid.UUID,
            workflow_config: WorkflowConfig,
            node_id: str,
            payload: dict[str, Any],
            run_id: str,
            messages: list[dict[str, Any]] | None = None,
            variable_pool=None,
    ) -> None:
        type_maps = self._build_snapshot_type_maps(workflow_config)
        debug_state = self._read_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        debug_snapshot = dict(debug_state["snapshot"])
        debug_snapshot["conversation"] = self._build_public_conversation_snapshot_from_variable_pool(
            workflow_config=workflow_config,
            variable_pool=variable_pool,
        ) or debug_snapshot.get("conversation") or {}
        nodes_snapshot = dict(debug_snapshot.get("nodes") or {})
        node_snapshot = self._build_public_node_snapshot_from_cache_result(
            {"output": payload.get("outputs")},
            type_map=type_maps["nodes"].get(node_id),
        )
        if node_snapshot:
            nodes_snapshot[node_id] = node_snapshot
        else:
            nodes_snapshot.pop(node_id, None)
        debug_snapshot["nodes"] = nodes_snapshot
        self._write_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
            snapshot=debug_snapshot,
            messages=messages,
            execution_id=run_id,
            source="single_node_debug",
        )

    def _serialize_last_node_execution(self, node_execution: WorkflowNodeExecution) -> dict[str, Any]:
        execution = node_execution.execution or (
            self.db.get(WorkflowExecution, node_execution.execution_id) if node_execution.execution_id else None
        )
        meta_data = node_execution.meta_data or {}
        source = meta_data.get("source", "workflow_execution")
        run_id = meta_data.get("run_id")
        workflow_execution_id = execution.execution_id if execution else None
        return {
            "node_id": node_execution.node_id,
            "node_type": node_execution.node_type,
            "node_name": node_execution.node_name,
            "status": node_execution.status,
            "source": source,
            # Keep execution_id for backward compatibility. For single-node runs it aliases run_id.
            "execution_id": workflow_execution_id or run_id or "",
            "run_id": run_id,
            "workflow_execution_id": workflow_execution_id,
            "inputs": node_execution.input_data,
            "outputs": node_execution.output_data,
            "process": meta_data.get("process_data"),
            "input_data": node_execution.input_data,
            "output_data": node_execution.output_data,
            "process_data": meta_data.get("process_data"),
            "agent_log": meta_data.get("agent_log"),
            "error_message": node_execution.error_message,
            "elapsed_time": node_execution.elapsed_time,
            "token_usage": node_execution.token_usage,
            "retry_count": node_execution.retry_count,
            "cache_hit": node_execution.cache_hit,
            "cache_key": node_execution.cache_key,
            "started_at": to_iso_z(node_execution.started_at),
            "completed_at": to_iso_z(node_execution.completed_at),
        }

    def get_node_last_run(
            self,
            app_id: uuid.UUID,
            node_id: str,
            source: str | None = None,
    ) -> dict[str, Any] | None:
        config = self.get_workflow_config(app_id)
        node_execution = self.node_execution_repo.get_latest_by_app_node(app_id, node_id, source=source)
        if not node_execution:
            return None

        secret_values = self._extract_secret_values_from_environment_variables(
            config.environment_variables if config else []
        )
        payload = self._serialize_last_node_execution(node_execution)
        return self._mask_payload_with_secret_values(payload, secret_values)

    @staticmethod
    def _build_node_cache_manager(
            *,
            app_id: uuid.UUID,
            workflow_config_id: uuid.UUID | None,
            node_id: str,
            node_type: str | None,
            node_name: str | None,
    ) -> WorkflowNodeCacheManager:
        return WorkflowNodeCacheManager(
            app_id=app_id,
            workflow_config_id=workflow_config_id,
            node_id=node_id,
            node_type=node_type or "unknown",
            node_name=node_name,
        )

    @staticmethod
    def _sanitize_cache_result_data(result_data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(result_data or {})
        for transient_key in (
            "cache_hit",
            "cache_key",
            "cache_id",
            "cache_status",
            "cache_hit_count",
            "cache_origin_elapsed_time",
        ):
            normalized.pop(transient_key, None)
        normalized.pop("execution_order", None)
        return normalize_cache_value(normalized)

    @staticmethod
    def _set_nested_patch_value(target: dict[str, Any], path: str, value: Any) -> None:
        keys = [item for item in path.split(".") if item]
        if not keys:
            raise ValueError("patch path 不能为空")
        current = target
        for key in keys[:-1]:
            nested = current.get(key)
            if not isinstance(nested, dict):
                nested = {}
                current[key] = nested
            current = nested
        current[keys[-1]] = normalize_cache_value(value)

    @classmethod
    def _apply_cache_result_patches(
            cls,
            result_data: dict[str, Any],
            patches: list[dict[str, Any]],
    ) -> dict[str, Any]:
        merged = cls._sanitize_cache_result_data(result_data)
        for patch in patches or []:
            scope = patch.get("scope", "output")
            selector = patch.get("path") or patch.get("name")
            if not selector:
                continue
            scope_value = merged.get(scope)
            if selector == "output" and not isinstance(scope_value, dict):
                merged[scope] = normalize_cache_value(patch.get("value"))
                continue
            if not isinstance(scope_value, dict):
                scope_value = {}
                merged[scope] = scope_value
            cls._set_nested_patch_value(scope_value, selector, patch.get("value"))
        return merged

    @staticmethod
    def _serialize_node_cache(cache: dict[str, Any] | None) -> dict[str, Any] | None:
        if not cache:
            return None
        return {
            "id": str(cache["id"]) if cache.get("id") else None,
            "node_id": cache.get("node_id"),
            "node_type": cache.get("node_type"),
            "node_name": cache.get("node_name"),
            "cache_key": cache.get("cache_key"),
            "source": cache.get("source"),
            "status": cache.get("status"),
            "input_data": cache.get("input_data"),
            "result_data": cache.get("result_data"),
            "hit_count": cache.get("hit_count", 0),
            "last_hit_at": cache.get("last_hit_at"),
            "expires_at": cache.get("expires_at"),
            "invalidated_at": cache.get("invalidated_at"),
            "created_at": cache.get("created_at"),
            "updated_at": cache.get("updated_at"),
            "meta_data": cache.get("meta_data") or {},
        }

    def get_node_cache(self, app_id: uuid.UUID, node_id: str) -> dict[str, Any] | None:
        config = self.get_workflow_config(app_id)
        node_name = self._get_node_name_from_config(config, node_id)
        node_type = next((node.get("type") for node in (config.nodes if config else []) if node.get("id") == node_id), None)
        manager = self._build_node_cache_manager(
            app_id=app_id,
            workflow_config_id=config.id if config else None,
            node_id=node_id,
            node_type=node_type,
            node_name=node_name,
        )
        cache = manager.get_latest_cache(include_inactive=False)
        secret_values = self._extract_secret_values_from_environment_variables(
            config.environment_variables if config else []
        )
        serialized = self._serialize_node_cache(cache)
        return self._mask_payload_with_secret_values(serialized, secret_values) if serialized else None

    def update_node_cache(
            self,
            *,
            app_id: uuid.UUID,
            node_id: str,
            result_data: dict[str, Any] | None = None,
            patches: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        config = self.get_workflow_config(app_id)
        if not config:
            return None
        node = next((item for item in config.nodes or [] if item.get("id") == node_id), None)
        if not node:
            return None
        manager = self._build_node_cache_manager(
            app_id=app_id,
            workflow_config_id=config.id,
            node_id=node_id,
            node_type=node.get("type"),
            node_name=node.get("name"),
        )
        latest_cache = manager.get_latest_cache(include_inactive=False)
        if not latest_cache:
            return None
        next_result_data = self._sanitize_cache_result_data(result_data or {})
        if patches:
            next_result_data = self._apply_cache_result_patches(
                latest_cache.get("result_data") or {},
                patches,
            )
        updated = manager.update_latest_cache(
            result_data=next_result_data,
        )
        if not updated:
            return None
        node_type_maps = self._build_snapshot_type_maps(config)["nodes"]
        self._sync_workflow_debug_state_node(
            app_id=app_id,
            workflow_config=config,
            node_id=node_id,
            node_snapshot=self._build_public_node_snapshot_from_cache_result(
            next_result_data,
            type_map=node_type_maps.get(node_id),
            ),
            source="cache_update",
        )
        serialized = self._serialize_node_cache(updated)
        secret_values = self._extract_secret_values_from_environment_variables(config.environment_variables)
        return self._mask_payload_with_secret_values(serialized, secret_values)

    def invalidate_node_cache(self, *, app_id: uuid.UUID, node_id: str) -> int:
        config = self.get_workflow_config(app_id)
        node = next((item for item in (config.nodes if config else []) or [] if item.get("id") == node_id), None)
        manager = self._build_node_cache_manager(
            app_id=app_id,
            workflow_config_id=config.id if config else None,
            node_id=node_id,
            node_type=node.get("type") if node else None,
            node_name=node.get("name") if node else None,
        )
        affected = manager.invalidate_latest_cache()
        if affected > 0:
            self._sync_workflow_debug_state_node(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                node_snapshot=None,
                source="cache_invalidate",
            )
        return affected

    def reset_workflow_debug_cache(self, *, app_id: uuid.UUID) -> dict[str, Any]:
        workflow_config = self.get_workflow_config(app_id)
        reset_snapshot = self._build_default_debug_state_snapshot(workflow_config)
        invalidated_caches = self.node_cache_repo.invalidate_by_app(
            app_id=app_id,
            invalidated_at=utcnow_naive(),
            exclude_node_ids=(self.DEBUG_STATE_NODE_ID,),
        )
        self.db.commit()
        self._write_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
            snapshot=reset_snapshot,
            messages=[],
            execution_id=None,
            source="reset",
        )
        secret_values = self._extract_secret_values_from_environment_variables(
            workflow_config.environment_variables if workflow_config else []
        )
        logger.info(
            "清空工作流调试缓存完成: app_id=%s, invalidated_caches=%s",
            app_id,
            invalidated_caches,
        )
        return {
            "invalidated_caches": invalidated_caches,
            "active_cache_count": 0,
            "execution_id": None,
            "source": "reset",
            "snapshot": self._mask_payload_with_secret_values(reset_snapshot, secret_values),
        }

    def get_workflow_debug_state(self, *, app_id: uuid.UUID) -> dict[str, Any]:
        workflow_config = self.get_workflow_config(app_id)
        debug_state = self._read_workflow_debug_state(
            app_id=app_id,
            workflow_config=workflow_config,
        )
        snapshot = debug_state["snapshot"]
        secret_values = self._extract_secret_values_from_environment_variables(
            workflow_config.environment_variables if workflow_config else []
        )
        return {
            "active_cache_count": len((snapshot.get("nodes") or {})) if isinstance(snapshot, dict) else 0,
            "execution_id": debug_state.get("execution_id"),
            "source": debug_state.get("source"),
            "snapshot": self._mask_payload_with_secret_values(snapshot, secret_values),
            "messages": self._mask_payload_with_secret_values(debug_state.get("messages") or [], secret_values),
        }

    def _has_single_node_debug_input(self, app_id: uuid.UUID, node_id: str) -> bool:
        node_execution = self.node_execution_repo.get_latest_by_app_node(
            app_id=app_id,
            node_id=node_id,
            source="single_node_debug",
        )
        if not node_execution:
            return False
        meta_data = node_execution.meta_data or {}
        return bool(isinstance(meta_data.get("debug_input"), dict) or isinstance(node_execution.input_data, dict))

    async def rerun_node_from_last_debug(
            self,
            *,
            app_id: uuid.UUID,
            node_id: str,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            invalidate_cache: bool = False,
            bypass_cache: bool = False,
    ) -> dict[str, Any]:
        node_execution = self.node_execution_repo.get_latest_by_app_node(
            app_id=app_id,
            node_id=node_id,
            source="single_node_debug",
        )
        if not node_execution:
            raise BusinessException("没有可用于重跑的单节点调试输入", BizCode.NOT_FOUND)
        meta_data = node_execution.meta_data or {}
        debug_input_data = meta_data.get("debug_input")
        if not isinstance(debug_input_data, dict):
            debug_input_data = {}
        if not debug_input_data and not isinstance(node_execution.input_data, dict):
            raise BusinessException("未找到可复用的单节点调试输入", BizCode.NOT_FOUND)
        if invalidate_cache:
            self.invalidate_node_cache(app_id=app_id, node_id=node_id)
        rerun_input = self._build_rerun_input_from_latest_state(
            app_id=app_id,
            workflow_config=config,
            node_type=node_execution.node_type,
            debug_input_data=debug_input_data,
        )
        rerun_input["bypass_cache"] = bypass_cache
        return await self.run_single_node(
            app_id=app_id,
            node_id=node_id,
            config=config,
            workspace_id=workspace_id,
            input_data=rerun_input,
        )

    def get_execution_detail(
            self,
            execution_id: str,
            app_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        execution = self.get_execution(execution_id)
        if not execution:
            return None
        if app_id and execution.app_id != app_id:
            return None

        node_executions = self.node_execution_repo.get_by_execution_id(execution.id)
        input_data = self._serialize_execution_value(execution.input_data or {})
        output_data = self._serialize_execution_value(execution.output_data or {})
        meta_data = self._serialize_execution_value(execution.meta_data or {})
        snapshot = self._build_execution_snapshot_from_record(execution)
        if self._get_execution_source(execution) == "single_node_debug":
            base_execution = self._resolve_base_execution(
                app_id=execution.app_id,
                debug_execution=execution,
            )
            if base_execution and base_execution.execution_id != execution.execution_id:
                snapshot = self._merge_execution_snapshots(
                    self._build_execution_snapshot_from_record(base_execution),
                    snapshot,
                )
        payload = {
            "execution_id": execution.execution_id,
            "app_id": str(execution.app_id),
            "workflow_config_id": str(execution.workflow_config_id),
            "release_id": str(execution.release_id) if execution.release_id else None,
            "conversation_id": str(execution.conversation_id) if execution.conversation_id else None,
            "trigger_type": execution.trigger_type,
            "status": execution.status,
            "input_data": input_data,
            "output_data": output_data,
            "snapshot": snapshot,
            "context": self._serialize_execution_value(execution.context or {}),
            "meta_data": meta_data,
            "error_message": execution.error_message,
            "error_node_id": execution.error_node_id,
            "started_at": to_iso_z(execution.started_at),
            "completed_at": to_iso_z(execution.completed_at),
            "elapsed_time": execution.elapsed_time,
            "token_usage": self._serialize_execution_value(execution.token_usage),
            "node_executions": [
                {
                    "node_id": node_execution.node_id,
                    "node_type": node_execution.node_type,
                    "node_name": node_execution.node_name,
                    "execution_order": node_execution.execution_order,
                    "retry_count": node_execution.retry_count,
                    "status": node_execution.status,
                    "input_data": self._serialize_execution_value(node_execution.input_data or {}),
                    "output_data": self._serialize_execution_value(node_execution.output_data or {}),
                    "agent_log": self._serialize_execution_value((node_execution.meta_data or {}).get("agent_log")),
                    "error_message": node_execution.error_message,
                    "started_at": to_iso_z(node_execution.started_at),
                    "completed_at": to_iso_z(node_execution.completed_at),
                    "elapsed_time": node_execution.elapsed_time,
                    "token_usage": self._serialize_execution_value(node_execution.token_usage),
                    "cache_hit": node_execution.cache_hit,
                    "cache_key": node_execution.cache_key,
                    "meta_data": self._serialize_execution_value(node_execution.meta_data or {}),
                }
                for node_execution in node_executions
            ],
        }
        workflow_config = execution.workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
        secret_values = self._extract_secret_values_from_environment_variables(
            workflow_config.environment_variables if workflow_config else []
        )
        return self._mask_payload_with_secret_values(payload, secret_values)

    def get_executions_by_app(
            self,
            app_id: uuid.UUID,
            limit: int = 50,
            offset: int = 0
    ) -> list[WorkflowExecution]:
        """获取应用的执行记录列表

        Args:
            app_id: 应用 ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            执行记录列表
        """
        return self.execution_repo.get_by_app_id(app_id, limit, offset)

    def update_execution_status(
            self,
            execution_id: str,
            status: str,
            token_usage: int | None = None,
            output_data: dict[str, Any] | None = None,
            error_message: str | None = None,
            error_node_id: str | None = None
    ) -> WorkflowExecution:
        """更新执行状态

        Args:
            execution_id: 执行 ID
            status: 状态
            token_usage: token消耗
            output_data: 输出数据
            error_message: 错误信息
            error_node_id: 出错节点 ID

        Returns:
            执行记录

        Raises:
            BusinessException: 执行记录不存在时抛出
        """
        execution = self.get_execution(execution_id)
        if not execution:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"执行记录不存在: execution_id={execution_id}"
            )

        execution.status = status
        if token_usage is not None:
            execution.token_usage = token_usage
        if output_data is not None:
            execution.output_data = convert_uuids_to_str(output_data)
        if error_message is not None:
            execution.error_message = error_message
        if error_node_id is not None:
            execution.error_node_id = error_node_id

        # 如果是完成状态，计算耗时
        if status in ["completed", "failed", "cancelled", "timeout"]:
            if not execution.completed_at:
                execution.completed_at = utcnow_naive()
                elapsed = (execution.completed_at - execution.started_at).total_seconds()
                execution.elapsed_time = elapsed

        self.db.commit()
        self.db.refresh(execution)

        logger.info(f"更新执行状态: execution_id={execution_id}, status={status}")
        return execution

    def get_execution_statistics(self, app_id: uuid.UUID) -> dict[str, Any]:
        """获取执行统计信息

        Args:
            app_id: 应用 ID

        Returns:
            统计信息
        """
        total = self.execution_repo.count_by_app_id(app_id)
        completed = self.execution_repo.count_by_status(app_id, "completed")
        failed = self.execution_repo.count_by_status(app_id, "failed")
        running = self.execution_repo.count_by_status(app_id, "running")

        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "success_rate": completed / total if total > 0 else 0
        }

    async def _resolve_variables_file_defaults(
            self,
            variables: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert FileInput-format defaults in workflow variables to full FileObject dicts."""
        from app.core.workflow.utils.file_processor import (
            resolve_local_file_object_dict,
            fetch_remote_file_meta,
        )

        async def _resolve_one(item: dict) -> dict | None:
            if not isinstance(item, dict) or item.get("is_file"):
                return item
            transfer_method = item.get("transfer_method", "remote_url")
            file_type = FileType.trans(item.get("type", "document"))
            origin_file_type = item.get("file_type") or file_type
            if transfer_method == "remote_url":
                url = item.get("url", "")
                return await fetch_remote_file_meta(url, file_type, origin_file_type) if url else None
            else:
                return resolve_local_file_object_dict(self.db, item.get("upload_file_id"), file_type, origin_file_type)

        result = []
        for var_def in variables:
            var_type = var_def.get("type", "")
            default = var_def.get("default")
            if var_type == "file" and isinstance(default, dict) and not default.get("is_file"):
                var_def = {**var_def, "default": await _resolve_one(default)}
            elif var_type == "array[file]" and isinstance(default, list):
                resolved = []
                for item in default:
                    r = await _resolve_one(item)
                    if r is not None:
                        resolved.append(r)
                var_def = {**var_def, "default": resolved}
            result.append(var_def)
        return result

    async def _handle_file_input(self, files: list[FileInput]):
        if not files:
            return []

        from app.core.workflow.utils.file_processor import (
            resolve_local_file_object_dict,
            build_file_object_dict_from_meta,
            fetch_remote_file_meta,
        )

        files_struct = []
        for file in files:
            url = await self.multimodal_service.get_file_url(file)
            file_type = str(file.type)
            origin_file_type = file.file_type or file_type

            if file.transfer_method.value == "local_file" and file.upload_file_id:
                fo = resolve_local_file_object_dict(self.db, file.upload_file_id, file_type, origin_file_type)
                files_struct.append(fo or build_file_object_dict_from_meta(
                    file_type=file_type, transfer_method="local_file",
                    origin_file_type=origin_file_type,
                    file_id=str(file.upload_file_id), url=url,
                    file_name=None, file_size=None, file_ext=None, content_type=None,
                ))
            else:
                files_struct.append(await fetch_remote_file_meta(url, file_type, origin_file_type))
        return files_struct

    async def _resolve_start_node_file_variables(
            self,
            start_node_vars: list[dict[str, Any]],
            variables: dict[str, Any]
    ) -> dict[str, Any]:
        """解析开始节点变量中文件类型的运行时值
        
        对于 type=file 或 type=array[file] 的变量，如果用户传入的是
        FileInput 格式的 dict/list，则将其转换为 FileObject 格式。
        
        Args:
            start_node_vars: 开始节点变量定义列表
            variables: 用户传入的变量值字典
            
        Returns:
            解析后的变量值字典
        """
        from app.core.workflow.utils.file_processor import (
            resolve_local_file_object_dict,
            build_file_object_dict_from_meta,
            fetch_remote_file_meta,
        )

        resolved = dict(variables)

        for var_def in start_node_vars:
            var_name = var_def.get("name")
            var_type = var_def.get("type", "")
            ui_type = var_def.get("ui_type")

            if ui_type not in ("file-upload", "file-list-upload"):
                continue

            if var_name not in resolved:
                continue

            value = resolved[var_name]

            if var_type == "file" and isinstance(value, dict) and not value.get("is_file"):
                url = value.get("url", "")
                transfer_method = value.get("transfer_method", "local_file" if value.get("upload_file_id") else "remote_url")
                file_type = str(FileType.trans(value.get("type", "document")))
                origin_file_type = value.get("file_type") or file_type

                if transfer_method == "local_file" and value.get("upload_file_id"):
                    fo = resolve_local_file_object_dict(self.db, value["upload_file_id"], file_type, origin_file_type)
                    resolved[var_name] = fo or build_file_object_dict_from_meta(
                        file_type=file_type, transfer_method="local_file",
                        origin_file_type=origin_file_type,
                        file_id=str(value["upload_file_id"]), url=url,
                    )
                elif url:
                    resolved[var_name] = await fetch_remote_file_meta(url, file_type, origin_file_type)

            elif var_type == "array[file]" and isinstance(value, list):
                resolved_list = []
                for item in value:
                    if isinstance(item, dict) and item.get("is_file"):
                        resolved_list.append(item)
                    elif isinstance(item, dict):
                        url = item.get("url", "")
                        transfer_method = item.get("transfer_method", "local_file" if item.get("upload_file_id") else "remote_url")
                        file_type = str(FileType.trans(item.get("type", "document")))
                        origin_file_type = item.get("file_type") or file_type

                        if transfer_method == "local_file" and item.get("upload_file_id"):
                            fo = resolve_local_file_object_dict(self.db, item["upload_file_id"], file_type, origin_file_type)
                            resolved_list.append(fo or build_file_object_dict_from_meta(
                                file_type=file_type, transfer_method="local_file",
                                origin_file_type=origin_file_type,
                                file_id=str(item["upload_file_id"]), url=url,
                            ))
                        elif url:
                            resolved_list.append(await fetch_remote_file_meta(url, file_type, origin_file_type))
                resolved[var_name] = resolved_list

        return resolved

    @staticmethod
    def _map_public_event(event: dict) -> dict | None:
        """
        Map internal workflow events to public-facing event formats.

        Purpose:
        - Hide internal execution details
        - Expose a stable and simplified public event schema
        - Filter out non-public events
        - Maintain backward compatibility when possible

        Args:
            event (dict): Internal event object, e.g.:
                {
                    "event": "workflow_start",
                    "data": {...}
                }

        Returns:
            dict | None:
                - Returns the mapped public event
                - Returns None if the event should not be exposed
        """
        event_type = event.get("event")
        payload = event.get("data")
        match event_type:
            case "workflow_start":
                return {
                    "event": "start",
                    "data": {
                        "conversation_id": payload.get("conversation_id"),
                        "message_id": payload.get("message_id")
                    }
                }
            case "workflow_end":
                data = {
                    "elapsed_time": payload.get("elapsed_time"),
                    "message_length": len(payload.get("output", "")),
                    "error": payload.get("error", "")
                }
                if "citations" in payload and payload["citations"]:
                    data["citations"] = payload["citations"]
                return {
                    "event": "end",
                    "data": data
                }
            case "node_start" | "node_end" | "node_error" | "cycle_item":
                return None
            case _:
                return event

    def _emit(self, public: bool, internal_event: dict):
        """
        Unified event emission entry.

        Args:
            public (bool):
                - True  -> Emit mapped public event
                - False -> Emit raw internal event

            internal_event (dict):
                The original internal event object

        Returns:
            dict | None:
                - The mapped event
                - Or None if the event is filtered out
        """
        if public:
            mapped = self._map_public_event(internal_event)
        else:
            mapped = internal_event
        return mapped

    def _get_memory_store_info(self, workspace_id: uuid.UUID) -> tuple[str, str]:
        storage_type = get_workspace_storage_type_without_auth(self.db, workspace_id)
        user_rag_memory_id = ""
        # 如果 storage_type 为 None，使用默认值 'neo4j'
        if not storage_type:
            storage_type = 'neo4j'
            logger.warning(
                f"Storage type not set for workspace {workspace_id}, using default: neo4j"
            )
        if storage_type == "rag":
            knowledge = knowledge_repository.get_knowledge_by_name(
                db=self.db,
                name="USER_RAG_MERORY",
                workspace_id=workspace_id
            )
            if knowledge:
                user_rag_memory_id = str(knowledge.id)
            else:
                logger.warning(
                    f"No knowledge base named 'USER_RAG_MEMORY' found, "
                    f"workspace_id: {workspace_id}, will use neo4j storage"
                )
                storage_type = 'neo4j'
        return storage_type, user_rag_memory_id

    @staticmethod
    def _extract_human_message_and_meta(
            final_messages: list[dict],
            fallback_message: str,
            fallback_files: list[dict] | None = None
    ) -> tuple[str, dict]:
        """从消息列表中提取用户消息和文件元数据，失败时回退到 payload 数据。"""
        human_message = fallback_message
        human_meta: dict = {"files": []}
        for message in final_messages:
            if message["role"] == "user":
                if isinstance(message["content"], str):
                    if not human_message:
                        human_message = message["content"]
                elif isinstance(message["content"], list):
                    for f in message["content"]:
                        human_meta["files"].append({
                            "type": f.get("type"),
                            "url": f.get("url"),
                            "file_type": f.get("origin_file_type"),
                            "name": f.get("name"),
                            "size": f.get("size"),
                        })
        if not human_meta["files"] and fallback_files:
            for f in fallback_files:
                human_meta["files"].append({
                    "type": f.get("type"),
                    "url": f.get("url"),
                    "file_type": f.get("origin_file_type"),
                    "name": f.get("name"),
                    "size": f.get("size"),
                })
        return human_message, human_meta

    def _save_failed_conversation(
            self,
            conversation_id: uuid.UUID | None,
            message_id: uuid.UUID | None,
            human_message: str,
            human_meta: dict,
            error: str,
    ) -> None:
        """将失败时的用户消息和助手错误消息写入会话。"""
        if not conversation_id:
            return
        self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="user",
            content=human_message,
            meta_data=human_meta,
            sync_memory=False,
        )
        self.conversation_service.add_message(
            **({"message_id": message_id} if message_id else {}),
            conversation_id=conversation_id,
            role="assistant",
            content="",
            meta_data={"error": error},
            sync_memory=False,
        )

    @staticmethod
    def _supports_conversation(config: WorkflowConfig) -> bool:
        return getattr(config, "workflow_type", "workflow") == "workflow"

    def _ensure_conversation(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: str | None,
            conversation_id: uuid.UUID | None,
            enable_conversation: bool,
    ) -> uuid.UUID | None:
        if not enable_conversation:
            return conversation_id
        if conversation_id:
            return conversation_id

        from app.models import Conversation as ConversationModel

        conversation_id = uuid.uuid4()
        new_conversation = ConversationModel(
            id=conversation_id,
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=True,
            title="草稿会话",
        )
        self.db.add(new_conversation)
        self.db.commit()
        self.db.refresh(new_conversation)
        return conversation_id

    def _get_history_info(self, conversation_id: uuid.UUID) -> tuple[dict, list] | None:
        executions = self.execution_repo.get_by_conversation_id(
            conversation_id=conversation_id,
            status="completed",
            limit_count=1
        )

        if executions:
            last_state = executions[0].output_data
            if isinstance(last_state, dict):
                variables = last_state.get("variables", {})
                conv_vars = variables.get("conv", {})
                conv_messages = last_state.get("messages") or []
                return conv_vars, conv_messages

        messages = self.conversation_service.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit=None
        )
        if messages:
            conv_messages = []
            for msg in messages:
                conv_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
            return {}, conv_messages

        return None

    # ==================== 工作流执行 ====================

    async def run(
            self,
            app_id: uuid.UUID,
            payload: DraftRunRequest,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            release_id: uuid.UUID | None = None,
            source: str = "",
            trigger_type: str = "manual",
            trigger_id: str | None = None,
            trigger_meta: dict[str, Any] | None = None,
            trigger_payload: dict[str, Any] | None = None,
    ):
        """运行工作流

        Args:
            release_id: 发布 ID
            workspace_id:工作空间 ID
            config: 配置
            payload:
            app_id: 应用 ID

        Returns:
            执行结果（非流式）

        Raises:
            BusinessException: 配置不存在或执行失败时抛出
        """
        # 1. 获取工作流配置
        if not config:
            config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.CONFIG_MISSING,
                message=f"工作流配置不存在: app_id={app_id}"
            )
        config.nodes = self._prepare_nodes(config.nodes)
        trigger_type, trigger_id, trigger_meta, trigger_payload = self._ensure_debug_trigger_args(
            config.nodes,
            trigger_type,
            trigger_id,
            trigger_meta,
            trigger_payload,
        )

        supports_conversation = self._supports_conversation(config)

        feature_configs = config.features or {}
        self._validate_file_upload(feature_configs, payload.files)

        # 解析开始节点文件类型的变量值
        start_node_vars = self.get_start_node_variables({"nodes": config.nodes})
        resolved_variables = await self._resolve_start_node_file_variables(
            start_node_vars, payload.variables or {}
        )

        input_data = {
            "variables": resolved_variables,
            "files": [file.model_dump(mode='json') for file in payload.files]
        }
        if payload.message is not None:
            input_data["message"] = payload.message
        if payload.conversation_id:
            input_data["conversation_id"] = payload.conversation_id
        input_data = self._merge_trigger_context(input_data, trigger_type, trigger_id, trigger_meta, trigger_payload)

        # 转换 conversation_id 为 UUID
        conversation_id_uuid = uuid.UUID(payload.conversation_id) if payload.conversation_id else None
        conversation_id_uuid = self._ensure_conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=payload.user_id,
            conversation_id=conversation_id_uuid,
            enable_conversation=supports_conversation,
        )
        if conversation_id_uuid:
            payload.conversation_id = str(conversation_id_uuid)
            input_data["conversation_id"] = str(conversation_id_uuid)

        # 检查标注命中 — 在创建工作流执行之前，命中则直接返回跳过整个工作流
        annotation_match = None
        if supports_conversation and payload.message:
            annotation_match = self._check_annotation_match(
                app_id, payload.message, source=source or HitLogSource.CONSOLE
            )
        if annotation_match:
            message_id = uuid.uuid4()
            prev_messages = []
            history = self._get_history_info(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                _, prev_messages = history
            self.conversation_service.add_message(
                conversation_id=conversation_id_uuid,
                role="user",
                content=payload.message,
                meta_data={"files": []}
            )
            self.conversation_service.add_message(
                message_id=message_id,
                conversation_id=conversation_id_uuid,
                role="assistant",
                content=annotation_match["answer"],
                meta_data={"usage": {}}
            )

            # 创建 WorkflowExecution 记录，用于日志显示
            execution = self.create_execution(
                workflow_config_id=config.id,
                app_id=app_id,
                trigger_type=trigger_type,
                triggered_by=None,
                conversation_id=conversation_id_uuid,
                input_data=input_data,
                release_id=release_id,
            )
            self._store_trigger_meta(execution, trigger_id, trigger_meta)
            execution.status = "completed"
            output_messages = prev_messages + [
                {"role": "user", "content": payload.message},
                {"role": "assistant", "content": annotation_match["answer"]}
            ]
            execution.output_data = {
                "answer": annotation_match["answer"],
                "messages": output_messages,
                "annotation_hit": {
                    "annotation_id": str(annotation_match["annotation_id"]),
                    "similarity": annotation_match["similarity"],
                    "question": annotation_match["question"],
                }
            }
            execution.token_usage = {}
            execution.elapsed_time = 0
            execution.completed_at = utcnow_naive()
            self.db.commit()

            return {
                "status": "completed",
                "answer": annotation_match["answer"],
                "messages": output_messages,
                "conversation_id": str(conversation_id_uuid),
                "token_usage": {},
                "elapsed_time": 0,
                "annotation_hit": {
                    "annotation_id": str(annotation_match["annotation_id"]),
                    "similarity": annotation_match["similarity"],
                    "question": annotation_match["question"],
                }
            }

        # 1.5 输入审查（仅对话式工作流 AppType.WORKFLOW）
        sensitive_cfg = feature_configs.get("sensitive_word_avoidance", {})
        if payload.message and sensitive_cfg.get("enabled"):
            from app.core.moderation.input_moderation import InputModeration
            moderation_type = sensitive_cfg.get("type", "keywords")
            stop, preset_response, _, _ = InputModeration().check(
                app_id=str(app_id),
                tenant_id=str(workspace_id),
                moderation_type=moderation_type,
                moderation_config=sensitive_cfg,
                inputs=payload.variables or {},
                query=payload.message,
            )
            if stop:
                message_id = uuid.uuid4()
                if conversation_id_uuid:
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="user",
                        content=payload.message,
                        meta_data={"files": []},
                    )
                    self.conversation_service.add_message(
                        message_id=message_id,
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=preset_response,
                        meta_data={"usage": {}, "moderation_flagged": True},
                    )
                execution = self.create_execution(
                    workflow_config_id=config.id,
                    app_id=app_id,
                    trigger_type="manual",
                    triggered_by=None,
                    conversation_id=conversation_id_uuid,
                    input_data={"message": payload.message, "moderation_flagged": True},
                    release_id=release_id,
                )
                execution.status = "completed"
                execution.output_data = {"answer": preset_response, "moderation_flagged": True}
                execution.elapsed_time = 0
                execution.completed_at = utcnow_naive()
                self.db.commit()
                return {
                    "execution_id": execution.execution_id,
                    "status": "completed",
                    "output": preset_response,
                    "message": preset_response,
                    "message_id": str(message_id),
                    "conversation_id": str(conversation_id_uuid) if conversation_id_uuid else None,
                    "error_message": None,
                    "elapsed_time": 0,
                    "token_usage": {},
                    "citations": [],
                    "moderation_flagged": True,
                }

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type=trigger_type,
            triggered_by=None,
            conversation_id=conversation_id_uuid,
            input_data=input_data,
            release_id=release_id,
        )
        self._store_trigger_meta(execution, trigger_id, trigger_meta)
        self.db.commit()

        # 3. 构建工作流配置字典
        workflow_config_dict = self._build_runtime_workflow_config_dict(
            app_id=app_id,
            workflow_config=config,
            features=feature_configs,
        )

        try:
            files = await self._handle_file_input(payload.files)
            storage_type, user_rag_memory_id = self._get_memory_store_info(workspace_id)
            input_data["files"] = files
            # 更新 execution.input_data，确保数据库中的 files 包含 URL
            execution.input_data = input_data
            self.db.commit()
            message_id = uuid.uuid4()
            # 更新状态为运行中
            self.update_execution_status(execution.execution_id, "running")

            history = self._get_history_info(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                conv_vars, conv_messages = history
                input_data["conv"] = conv_vars
                input_data["conv_messages"] = conv_messages
            init_message_length = len(input_data.get("conv_messages", []))

            # 新会话时写入开场白
            is_new_conversation = init_message_length == 0
            if is_new_conversation:
                opening_cfg = feature_configs.get("opening_statement", {})
                if (
                        conversation_id_uuid
                        and isinstance(opening_cfg, dict)
                        and opening_cfg.get("enabled")
                        and opening_cfg.get("statement")
                ):
                    statement = opening_cfg["statement"]
                    suggested_questions = opening_cfg.get("suggested_questions", [])
                    if payload.variables:
                        for var_name, var_value in payload.variables.items():
                            statement = statement.replace(f"{{{{{var_name}}}}}", str(var_value))
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=statement,
                        meta_data={"suggested_questions": suggested_questions},
                        sync_memory=False,
                    )
                    # 注入到 conv_messages，让 LLM 感知开场白
                    input_data["conv_messages"] = [{"role": "assistant", "content": statement}]
                    init_message_length = 1

            result = await execute_workflow(
                workflow_config=workflow_config_dict,
                input_data=input_data,
                execution_id=execution.execution_id,
                workspace_id=str(workspace_id),
                user_id=payload.user_id,
                memory_storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id
            )

            # 输出审查（非流式）
            if result.get("status") == "completed" and sensitive_cfg.get("enabled"):
                outputs_enabled = sensitive_cfg.get("config", {}).get("outputs_config", {}).get("enabled", False)
                if outputs_enabled:
                    from app.core.moderation.output_moderation import OutputModeration
                    output_moderation = OutputModeration(
                        app_id=str(app_id),
                        tenant_id=str(workspace_id),
                        moderation_type=sensitive_cfg.get("type", "keywords"),
                        moderation_config=sensitive_cfg,
                    )
                    output_text = result.get("output", "")
                    output_moderation.accumulate(output_text)
                    if output_moderation.check_final():
                        result["output"] = output_moderation.preset_response
                        result["preset_response"] = output_moderation.preset_response
                        result["moderation_flagged"] = True

            # 更新执行结果
            if result.get("status") == "completed":
                token_usage = result.get("token_usage", {}) or {}

                final_messages = result.get("messages", [])[init_message_length:]
                human_message = ""
                assistant_message = ""
                human_meta = {
                    "files": []
                }
                for message in final_messages:
                    if message["role"] == "user":
                        if isinstance(message["content"], str):
                            human_message += message["content"]
                        elif isinstance(message["content"], list):
                            for file in message["content"]:
                                human_meta["files"].append({
                                    "type": file.get("type"),
                                    "url": file.get("url"),
                                    "file_type": file.get("origin_file_type"),
                                    "name": file.get("name"),
                                    "size": file.get("size"),
                                })
                    if message["role"] == "assistant":
                        assistant_message = message["content"]
                if conversation_id_uuid:
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="user",
                        content=human_message,
                        meta_data=human_meta,
                        sync_memory=False,
                    )
                # 过滤 citations
                citations = result.get("citations", [])
                citation_cfg = feature_configs.get("citation", {})
                if isinstance(citation_cfg, dict) and citation_cfg.get("enabled"):
                    allow_download = citation_cfg.get("allow_download", False)
                    if allow_download:
                        from app.core.config import settings
                        for c in citations:
                            if c.get("document_id"):
                                c["download_url"] = f"{settings.FILE_LOCAL_SERVER_URL}/apps/citations/{c['document_id']}/download"
                    filtered_citations = citations
                else:
                    filtered_citations = []
                assistant_meta = {"usage": token_usage, "audio_url": None}
                if filtered_citations:
                    assistant_meta["citations"] = filtered_citations
                if conversation_id_uuid:
                    self.conversation_service.add_message(
                        message_id=message_id,
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=assistant_message,
                        meta_data=assistant_meta,
                        sync_memory=False,
                    )
                self.update_execution_status(
                    execution.execution_id,
                    "completed",
                    output_data=result,
                    token_usage=token_usage.get("total_tokens", None)
                )
                execution = self.get_execution(execution.execution_id)
                self._persist_workflow_node_executions(execution, config, result)
                self._refresh_workflow_debug_state_from_execution(execution)

                logger.info(f"Workflow Run Success, "
                            f"execution_id: {execution.execution_id}, message count: {len(final_messages)}")
            else:
                self.update_execution_status(
                    execution.execution_id,
                    "failed",
                    error_message=result.get("error")
                )
                execution = self.get_execution(execution.execution_id)
                self._persist_workflow_node_executions(execution, config, result)
                self._refresh_workflow_debug_state_from_execution(execution)
                logger.error(f"Workflow Run Failed, execution_id: {execution.execution_id},"
                             f" error: {result.get('error')}")
                final_messages = result.get("messages", [])[init_message_length:]
                human_message, human_meta = self._extract_human_message_and_meta(
                    final_messages, payload.message or "", files
                )
                self._save_failed_conversation(
                    conversation_id_uuid, message_id, human_message, human_meta, result.get("error") or ""
                )
                filtered_citations = []

            # 返回增强的响应结构
            return {
                "execution_id": execution.execution_id,
                "status": result.get("status"),
                # "variables": result.get("variables"),
                # "messages": result.get("messages"),
                "output": result.get("output"),  # 最终输出（字符串）
                "message": result.get("output"),  # 最终输出（字符串）
                "message_id": str(message_id),
                # "output_data": result.get("node_outputs", {}),  # 所有节点输出（详细数据）
                "conversation_id": result.get("conversation_id") or (str(conversation_id_uuid) if conversation_id_uuid else None),
                "error_message": result.get("error"),
                "elapsed_time": result.get("elapsed_time"),
                "token_usage": result.get("token_usage"),
                "citations": filtered_citations,
            }

        except Exception as e:
            logger.error(f"工作流执行失败: execution_id={execution.execution_id}, error={e}", exc_info=True)
            self.update_execution_status(execution.execution_id, "failed", error_message=str(e))
            human_message, human_meta = self._extract_human_message_and_meta([], payload.message or "", files)
            self._save_failed_conversation(conversation_id_uuid, None, human_message, human_meta, str(e))
            raise BusinessException(
                code=BizCode.INTERNAL_ERROR,
                message=f"工作流执行失败: {str(e)}"
            )

    async def run_stream(
            self,
            app_id: uuid.UUID,
            payload: DraftRunRequest,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            release_id: Optional[uuid.UUID] = None,
            public: bool = False,
            source: str = "",
            trigger_type: str = "manual",
            trigger_id: str | None = None,
            trigger_meta: dict[str, Any] | None = None,
            trigger_payload: dict[str, Any] | None = None,
    ):
        """运行工作流（流式）

        Args:
            release_id: 发布id
            workspace_id:
            app_id: 应用 ID
            payload: 请求对象（包含 message, variables, conversation_id 等）
            config: 存储类型（可选）
            public: 是否发布

        Yields:
            SSE 格式的流式事件

        Raises:
            BusinessException: 配置不存在或执行失败时抛出
        """
        # 1. 获取工作流配置
        if not config:
            config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.CONFIG_MISSING,
                message=f"工作流配置不存在: app_id={app_id}"
            )
        config.nodes = self._prepare_nodes(config.nodes)
        trigger_type, trigger_id, trigger_meta, trigger_payload = self._ensure_debug_trigger_args(
            config.nodes,
            trigger_type,
            trigger_id,
            trigger_meta,
            trigger_payload,
        )
        supports_conversation = self._supports_conversation(config)
        feature_configs = config.features or {}
        self._validate_file_upload(feature_configs, payload.files)

        # 解析开始节点文件类型的变量值
        start_node_vars = self.get_start_node_variables({"nodes": config.nodes})
        resolved_variables = await self._resolve_start_node_file_variables(
            start_node_vars, payload.variables or {}
        )

        input_data = {
            "variables": resolved_variables,
            "files": [file.model_dump(mode='json') for file in payload.files]
        }
        if payload.message is not None:
            input_data["message"] = payload.message
        if payload.conversation_id:
            input_data["conversation_id"] = payload.conversation_id
        input_data = self._merge_trigger_context(input_data, trigger_type, trigger_id, trigger_meta, trigger_payload)

        # 转换 conversation_id 为 UUID
        conversation_id_uuid = uuid.UUID(payload.conversation_id) if payload.conversation_id else None
        conversation_id_uuid = self._ensure_conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=payload.user_id,
            conversation_id=conversation_id_uuid,
            enable_conversation=supports_conversation,
        )
        if conversation_id_uuid:
            payload.conversation_id = str(conversation_id_uuid)
            input_data["conversation_id"] = str(conversation_id_uuid)

        # 检查标注命中 — 在创建工作流执行之前，命中则直接返回跳过整个工作流
        annotation_match = None
        if supports_conversation and payload.message:
            annotation_match = self._check_annotation_match(
                app_id, payload.message, source=source or HitLogSource.CONSOLE
            )
        if annotation_match:
            message_id = uuid.uuid4()
            prev_messages = []
            history = self._get_history_info(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                _, prev_messages = history
            self.conversation_service.add_message(
                conversation_id=conversation_id_uuid,
                role="user",
                content=payload.message,
                meta_data={"files": []}
            )
            self.conversation_service.add_message(
                message_id=message_id,
                conversation_id=conversation_id_uuid,
                role="assistant",
                content=annotation_match["answer"],
                meta_data={"usage": {}}
            )

            # 创建 WorkflowExecution 记录，用于日志显示
            execution = self.create_execution(
                workflow_config_id=config.id,
                app_id=app_id,
                trigger_type=trigger_type,
                triggered_by=None,
                conversation_id=conversation_id_uuid,
                input_data=input_data,
                release_id=release_id,
            )
            self._store_trigger_meta(execution, trigger_id, trigger_meta)
            execution.status = "completed"
            output_messages = prev_messages + [
                {"role": "user", "content": payload.message},
                {"role": "assistant", "content": annotation_match["answer"]}
            ]
            execution.output_data = {
                "answer": annotation_match["answer"],
                "messages": output_messages,
                "annotation_hit": {
                    "annotation_id": str(annotation_match["annotation_id"]),
                    "similarity": annotation_match["similarity"],
                    "question": annotation_match["question"],
                }
            }
            execution.token_usage = {}
            execution.elapsed_time = 0
            execution.completed_at = utcnow_naive()
            self.db.commit()

            start_internal_event = {
                "event": "workflow_start",
                "data": {
                    "conversation_id": str(conversation_id_uuid),
                    "message_id": str(message_id)
                }
            }
            start_event = self._emit(public, start_internal_event)
            if start_event:
                yield start_event

            answer = annotation_match["answer"]
            yield {
                "event": "message",
                "data": {
                    "content": answer,
                    "conversation_id": str(conversation_id_uuid),
                }
            }

            end_internal_event = {
                "event": "workflow_end",
                "data": {
                    "execution_id": execution.execution_id,
                    "conversation_id": str(conversation_id_uuid),
                    "output": answer,
                    "usage": {},
                    "elapsed_time": 0,
                    "status": "completed",
                    "error": "",
                    "annotation_hit": {
                        "annotation_id": str(annotation_match["annotation_id"]),
                        "similarity": annotation_match["similarity"],
                        "question": annotation_match["question"],
                    }
                }
            }
            end_event = self._emit(public, end_internal_event)
            if end_event:
                yield end_event
            return

        # 1.5 输入审查（仅对话式工作流 AppType.WORKFLOW）
        sensitive_cfg = feature_configs.get("sensitive_word_avoidance", {})
        if payload.message and sensitive_cfg.get("enabled"):
            from app.core.moderation.input_moderation import InputModeration
            moderation_type = sensitive_cfg.get("type", "keywords")
            stop, preset_response, _, _ = InputModeration().check(
                app_id=str(app_id),
                tenant_id=str(workspace_id),
                moderation_type=moderation_type,
                moderation_config=sensitive_cfg,
                inputs=payload.variables or {},
                query=payload.message,
            )
            if stop:
                message_id = uuid.uuid4()
                if conversation_id_uuid:
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="user",
                        content=payload.message,
                        meta_data={"files": []},
                    )
                    self.conversation_service.add_message(
                        message_id=message_id,
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=preset_response,
                        meta_data={"usage": {}, "moderation_flagged": True},
                    )
                execution = self.create_execution(
                    workflow_config_id=config.id,
                    app_id=app_id,
                    trigger_type="manual",
                    triggered_by=None,
                    conversation_id=conversation_id_uuid,
                    input_data={"message": payload.message, "moderation_flagged": True},
                    release_id=release_id,
                )
                execution.status = "completed"
                execution.output_data = {"answer": preset_response, "moderation_flagged": True}
                execution.elapsed_time = 0
                execution.completed_at = utcnow_naive()
                self.db.commit()

                start_internal_event = {
                    "event": "workflow_start",
                    "data": {
                        "conversation_id": str(conversation_id_uuid),
                        "message_id": str(message_id)
                    }
                }
                start_event = self._emit(public, start_internal_event)
                if start_event:
                    yield start_event

                yield {
                    "event": "message",
                    "data": {
                        "content": preset_response,
                        "conversation_id": str(conversation_id_uuid),
                    }
                }

                end_internal_event = {
                    "event": "workflow_end",
                    "data": {
                        "execution_id": execution.execution_id,
                        "conversation_id": str(conversation_id_uuid),
                        "output": preset_response,
                        "usage": {},
                        "elapsed_time": 0,
                        "status": "completed",
                        "error": "",
                        "moderation_flagged": True,
                    }
                }
                end_event = self._emit(public, end_internal_event)
                if end_event:
                    yield end_event
                return

        # 2. 创建执行记录
        execution = self.create_execution(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type=trigger_type,
            triggered_by=None,
            conversation_id=conversation_id_uuid,
            input_data=input_data,
            release_id=release_id,
        )
        self._store_trigger_meta(execution, trigger_id, trigger_meta)
        self.db.commit()

        # 3. 构建工作流配置字典
        workflow_config_dict = self._build_runtime_workflow_config_dict(
            app_id=app_id,
            workflow_config=config,
            features=feature_configs,
        )

        try:
            files = await self._handle_file_input(payload.files)
            storage_type, user_rag_memory_id = self._get_memory_store_info(workspace_id)
            input_data["files"] = files
            # 更新 execution.input_data，确保数据库中的 files 包含 URL
            execution.input_data = input_data
            self.db.commit()
            self.update_execution_status(execution.execution_id, "running")
            history = self._get_history_info(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                conv_vars, conv_messages = history
                input_data["conv"] = conv_vars
                input_data["conv_messages"] = conv_messages
            init_message_length = len(input_data.get("conv_messages", []))
            message_id = uuid.uuid4()
            _cycle_items: dict[str, list] = {}

            # 新会话时写入开场白
            is_new_conversation = init_message_length == 0
            if is_new_conversation:
                opening_cfg = feature_configs.get("opening_statement", {})
                if (
                        conversation_id_uuid
                        and isinstance(opening_cfg, dict)
                        and opening_cfg.get("enabled")
                        and opening_cfg.get("statement")
                ):
                    statement = opening_cfg["statement"]
                    suggested_questions = opening_cfg.get("suggested_questions", [])
                    if payload.variables:
                        for var_name, var_value in payload.variables.items():
                            statement = statement.replace(f"{{{{{var_name}}}}}", str(var_value))
                    self.conversation_service.add_message(
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=statement,
                        meta_data={"suggested_questions": suggested_questions},
                        sync_memory=False,
                    )
                    # 注入到 conv_messages，让 LLM 感知开场白
                    input_data["conv_messages"] = [{"role": "assistant", "content": statement}]
                    init_message_length = 1

            output_moderation = None
            llm_node_ids: set[str] = set()
            if sensitive_cfg.get("enabled"):
                outputs_enabled = sensitive_cfg.get("config", {}).get("outputs_config", {}).get("enabled", False)
                if outputs_enabled:
                    from app.core.moderation.output_moderation import OutputModeration
                    output_moderation = OutputModeration(
                        app_id=str(app_id),
                        tenant_id=str(workspace_id),
                        moderation_type=sensitive_cfg.get("type", "keywords"),
                        moderation_config=sensitive_cfg,
                    )
                    for node in (config.nodes or []):
                        if node.get("type") == "llm":
                            llm_node_ids.add(node.get("id", ""))


            moderation_flagged = False
            active_llm_nodes: set[str] = set()  # 已 node_start 但尚未 node_end 的 LLM 节点

            async for event in execute_workflow_stream(
                    workflow_config=workflow_config_dict,
                    input_data=input_data,
                    execution_id=execution.execution_id,
                    workspace_id=str(workspace_id),
                    user_id=payload.user_id,
                    memory_storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id
            ):
                event_type = event.get("event")
                event_data = event.get("data", {})

                # 审查触发后，跳过所有后续 message 事件（不再向客户端输出模型流式文本）
                if moderation_flagged and event_type == "message":
                    continue

                # message 事件：先审查再 yield，检测到关键词立即触发 message_replace 并结束流式
                if event_type == "message" and output_moderation and not moderation_flagged:
                    chunk = event_data.get("content", "")
                    if output_moderation.accumulate(chunk):
                        moderation_flagged = True
                        yield {"event": "message_replace",
                               "data": {"content": output_moderation.preset_response}}

                        # 审查触发后，立即保存对话、更新执行状态，合成结束事件并退出循环
                        preset_response = output_moderation.preset_response
                        human_message, human_meta = self._extract_human_message_and_meta(
                            [], payload.message or "", files
                        )
                        if conversation_id_uuid:
                            self.conversation_service.add_message(
                                conversation_id=conversation_id_uuid,
                                role="user",
                                content=human_message,
                                meta_data=human_meta,
                                sync_memory=False,
                            )
                            self.conversation_service.add_message(
                                message_id=message_id,
                                conversation_id=conversation_id_uuid,
                                role="assistant",
                                content=preset_response,
                                meta_data={"usage": {}, "audio_url": None, "moderation_flagged": True},
                                sync_memory=False,
                            )
                        # 更新执行状态为完成，标记审查触发
                        workflow_output_data = {
                            "moderation_flagged": True,
                            "preset_response": preset_response,
                        }
                        if _cycle_items and execution.output_data:
                            import copy
                            new_output_data = copy.deepcopy(execution.output_data)
                            node_outputs = new_output_data.setdefault("node_outputs", {})
                            for cycle_node_id, items in _cycle_items.items():
                                if cycle_node_id in node_outputs:
                                    node_outputs[cycle_node_id]["cycle_items"] = items
                                else:
                                    node_outputs[cycle_node_id] = {"cycle_items": items}
                            workflow_output_data.update(new_output_data)
                        self.update_execution_status(
                            execution.execution_id,
                            "completed",
                            output_data=workflow_output_data,
                        )
                        # 合成 LLM node_end 事件：只为当前正在执行的 LLM 节点补发
                        import datetime as _dt
                        for llm_node_id in active_llm_nodes:
                            node_end_event = {
                                "event": "node_end",
                                "data": {
                                    "node_id": llm_node_id,
                                    "conversation_id": str(conversation_id_uuid) if conversation_id_uuid else None,
                                    "execution_id": execution.execution_id,
                                    "timestamp": int(_dt.datetime.now().timestamp() * 1000),
                                    "output": preset_response,
                                    "elapsed_time": 0,
                                }
                            }
                            node_end_mapped = self._emit(public, node_end_event)
                            if node_end_mapped:
                                yield node_end_mapped
                        # 合成 workflow_end 事件并退出，不再等待 LLM 静默执行完成
                        end_internal_event = {
                            "event": "workflow_end",
                            "data": {
                                "conversation_id": str(conversation_id_uuid) if conversation_id_uuid else None,
                                "output": preset_response,
                                "usage": {},
                                "elapsed_time": 0,
                                "status": "completed",
                                "error": "",
                                "moderation_flagged": True,
                            }
                        }
                        end_event = self._emit(public, end_internal_event)
                        if end_event:
                            yield end_event
                        break

                if event_type == "cycle_item":
                    cycle_id = event_data.get("cycle_id")
                    if cycle_id not in _cycle_items:
                        _cycle_items[cycle_id] = []
                    _cycle_items[cycle_id].append(event_data)

                if event.get("event") == "workflow_end":
                    status = event.get("data", {}).get("status")
                    token_usage = event.get("data", {}).get("token_usage", {}) or {}
                    if status == "completed":
                        final_messages = event.get("data", {}).get("messages", [])[init_message_length:]
                        human_message = ""
                        assistant_message = ""
                        human_meta = {
                            "files": []
                        }
                        for message in final_messages:
                            if message["role"] == "user":
                                if isinstance(message["content"], str):
                                    human_message += message["content"]
                                elif isinstance(message["content"], list):
                                    for file in message["content"]:
                                        human_meta["files"].append({
                                            "type": file.get("type"),
                                            "url": file.get("url"),
                                            "file_type": file.get("origin_file_type"),
                                            "name": file.get("name"),
                                            "size": file.get("size")
                                        })
                            if message["role"] == "assistant":
                                assistant_message = message["content"]
                        if conversation_id_uuid:
                            self.conversation_service.add_message(
                                conversation_id=conversation_id_uuid,
                                role="user",
                                content=human_message,
                                meta_data=human_meta,
                                sync_memory=False,
                            )
                        # 过滤 citations
                        citations = event.get("data", {}).get("citations", [])
                        citation_cfg = feature_configs.get("citation", {})
                        if isinstance(citation_cfg, dict) and citation_cfg.get("enabled"):
                            allow_download = citation_cfg.get("allow_download", False)
                            if allow_download:
                                from app.core.config import settings
                                for c in citations:
                                    if c.get("document_id"):
                                        c["download_url"] = f"{settings.FILE_LOCAL_SERVER_URL}/apps/citations/{c['document_id']}/download"
                            filtered_citations = citations
                        else:
                            filtered_citations = []
                        assistant_meta = {"usage": token_usage, "audio_url": None}
                        if filtered_citations:
                            assistant_meta["citations"] = filtered_citations
                        if conversation_id_uuid:
                            self.conversation_service.add_message(
                                message_id=message_id,
                                conversation_id=conversation_id_uuid,
                                role="assistant",
                                content=assistant_message,
                                meta_data=assistant_meta,
                                sync_memory=False,
                            )
                        # 输出审查触发时，将 moderation 信息写入 execution output_data
                        workflow_output_data = event.get("data") or {}
                        if output_moderation and output_moderation.is_flagged:
                            workflow_output_data["moderation_flagged"] = True
                            workflow_output_data["preset_response"] = output_moderation.preset_response
                        self.update_execution_status(
                            execution.execution_id,
                            "completed",
                            output_data=workflow_output_data,
                            token_usage=token_usage.get("total_tokens", None)
                        )
                        event.setdefault("data", {})["citations"] = filtered_citations
                        logger.info(f"Workflow Run Success, "
                                    f"execution_id: {execution.execution_id}, message count: {len(final_messages)}")
                    elif status == "failed":
                        error_msg = event.get("data", {}).get("error", "未知错误")
                        final_messages = event.get("data", {}).get("messages", [])[init_message_length:]
                        human_message, human_meta = self._extract_human_message_and_meta(
                            final_messages, payload.message or "", files
                        )
                        self._save_failed_conversation(
                            conversation_id_uuid, message_id, human_message, human_meta, error_msg
                        )
                        self.update_execution_status(
                            execution.execution_id, "failed", output_data=event.get("data")
                        )
                    else:
                        logger.error(f"unexpect workflow run status, status: {status}")
                    # 把积累的 cycle_item 写入 workflow_executions.output_data["node_outputs"]
                    if _cycle_items and execution.output_data:
                        import copy
                        new_output_data = copy.deepcopy(execution.output_data)
                        node_outputs = new_output_data.setdefault("node_outputs", {})
                        for cycle_node_id, items in _cycle_items.items():
                            if cycle_node_id in node_outputs:
                                node_outputs[cycle_node_id]["cycle_items"] = items
                            else:
                                node_outputs[cycle_node_id] = {"cycle_items": items}
                        execution.output_data = new_output_data
                        self.db.commit()
                    if status in {"completed", "failed"} and execution.output_data:
                        self._persist_workflow_node_executions(execution, config, execution.output_data)
                        self._refresh_workflow_debug_state_from_execution(execution)
                elif event.get("event") == "workflow_start":
                    event["data"]["message_id"] = str(message_id)
                # 记录活跃 LLM 节点：node_start 加入，node_end 移除，合成时只为活跃节点补发
                if event_type == "node_start" and event_data.get("node_id") in llm_node_ids:
                    active_llm_nodes.add(event_data.get("node_id"))
                if event_type == "node_end" and event_data.get("node_id") in llm_node_ids:
                    active_llm_nodes.discard(event_data.get("node_id"))
                event = self._emit(public, event)
                if event:
                    yield event

        except Exception as e:
            logger.error(
                f"Workflow streaming execution failed: execution_id={execution.execution_id}, error={e}",
                exc_info=True
            )
            self.update_execution_status(execution.execution_id, "failed", error_message=str(e))
            human_message, human_meta = self._extract_human_message_and_meta([], payload.message or "", files)
            self._save_failed_conversation(conversation_id_uuid, None, human_message, human_meta, str(e))
            yield {"event": "error", "data": {"execution_id": execution.execution_id, "error": str(e)}}

    async def _build_node_context(
            self,
            app_id: uuid.UUID,
            node_id: str,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any],
    ):
        """构建单节点执行所需的上下文（node_config, node, state, variable_pool）"""
        from app.core.workflow.engine.runtime_schema import ExecutionContext
        from app.core.workflow.engine.variable_pool import VariablePool, VariablePoolInitializer
        from app.core.workflow.engine.state_manager import WorkflowState
        from app.core.workflow.nodes.node_factory import NodeFactory

        if not config:
            config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(code=BizCode.CONFIG_MISSING, message="工作流配置不存在")

        input_data = self._ensure_debug_trigger_input_data(
            config.nodes,
            input_data,
            preferred_node_id=node_id,
        )

        node_config = next((n for n in config.nodes if n.get("id") == node_id), None)
        if not node_config:
            raise BusinessException(code=BizCode.NOT_FOUND, message=f"节点不存在: node_id={node_id}")

        runtime_seed = self._build_single_node_runtime_seed(
            app_id=app_id,
            workflow_config=config,
        )
        input_data = await self._prepare_single_node_start_input(
            node_config=node_config,
            input_data=input_data,
        )

        workflow_config_dict = self._build_runtime_workflow_config_dict(
            app_id=app_id,
            workflow_config=config,
            runtime_options={
                "bypass_node_cache": bool(input_data.get("bypass_cache")),
                "cache_source": "single_node_debug",
            },
        )

        storage_type, user_rag_memory_id = self._get_memory_store_info(workspace_id)
        execution_id = self._build_single_node_execution_id()
        execution_context = self._build_execution_context(
            ExecutionContext,
            execution_id=execution_id,
            workspace_id=workspace_id,
            input_data=input_data,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )

        input_data = await self._prepare_single_node_input_files(input_data)

        variable_pool = VariablePool()
        await VariablePoolInitializer(workflow_config_dict).initialize(variable_pool, input_data, execution_context)
        await self._restore_variable_pool_from_snapshot(
            variable_pool=variable_pool,
            snapshot=runtime_seed["runtime_snapshot"],
        )
        await self._apply_input_data_overrides_to_variable_pool(
            variable_pool=variable_pool,
            input_data=input_data,
        )
        await self._inject_single_node_flat_inputs(
            variable_pool=variable_pool,
            input_data=input_data,
        )
        state = self._build_single_node_workflow_state(
            workflow_state_cls=WorkflowState,
            node_id=node_id,
            execution_id=execution_id,
            workspace_id=workspace_id,
            input_data=input_data,
            workflow_config_dict=workflow_config_dict,
            runtime_seed=runtime_seed,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )

        node = NodeFactory.create_node(node_config, workflow_config_dict, [])
        return node_config, node, state, variable_pool

    async def run_single_node(
            self,
            app_id: uuid.UUID,
            node_id: str,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """单节点执行（非流式）"""
        input_data = input_data or {}
        node_config, node, state, variable_pool = await self._build_node_context(
            app_id, node_id, config, workspace_id, input_data
        )
        run_id = self._build_single_node_run_id()
        try:
            state_update = await node.run(state, variable_pool)
            node_output = (state_update.get("node_outputs") or {}).get(node_id) or {}
            payload = self._build_single_node_payload_from_node_output(
                node_id=node_id,
                node_type=node_config.get("type"),
                node_output=node_output,
            )
            payload = self._attach_debug_execution_id(payload, run_id)
            self._persist_single_node_execution(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                node_type=node_config.get("type"),
                node_name=node_config.get("name"),
                payload=payload,
                run_id=run_id,
                debug_input_data=input_data,
            )
            self._refresh_workflow_debug_state_from_single_node_payload(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                payload=payload,
                run_id=run_id,
                messages=state.get("messages"),
                variable_pool=variable_pool,
            )
            return self._mask_runtime_secrets(payload, variable_pool)
        except Exception as e:
            logger.error(f"单节点执行失败: node_id={node_id}, error={e}", exc_info=True)
            payload = {
                "status": "failed",
                "execution_id": run_id,
                "node_id": node_id,
                "node_type": node_config.get("type"),
                "inputs": node._extract_input(state, variable_pool),
                "outputs": None,
                "token_usage": None,
                "elapsed_time": None,
                "error": str(e),
            }
            self._persist_single_node_execution(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                node_type=node_config.get("type"),
                node_name=node_config.get("name"),
                payload=payload,
                run_id=run_id,
                debug_input_data=input_data,
            )
            return self._mask_runtime_secrets(payload, variable_pool)

    async def run_single_node_stream(
            self,
            app_id: uuid.UUID,
            node_id: str,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any] | None = None,
    ):
        """单节点执行（流式）

        Yields:
            node_start -> node_chunk（LLM 等流式节点）-> node_end / node_error
        """
        input_data = input_data or {}
        node_config, node, state, variable_pool = await self._build_node_context(
            app_id, node_id, config, workspace_id, input_data
        )
        node_type = node_config.get("type")
        run_id = self._build_single_node_run_id()
        start_time = time.time()

        yield {
            "event": "node_start",
            "data": {
                "node_id": node_id,
                "node_type": node_type,
                "execution_id": run_id,
            }
        }

        final_result = None
        try:
            cached_state_update = await node._try_use_cache(state, variable_pool, start_time)
            if cached_state_update:
                node_output = (cached_state_update.get("node_outputs") or {}).get(node_id) or {}
                event_payload = {
                    "event": "node_end",
                    "data": self._attach_debug_execution_id(
                        self._build_single_node_payload_from_node_output(
                            node_id=node_id,
                            node_type=node_type,
                            node_output=node_output,
                        ),
                        run_id,
                    )
                }
                self._persist_single_node_execution(
                    app_id=app_id,
                    workflow_config=config,
                    node_id=node_id,
                    node_type=node_type,
                    node_name=node_config.get("name"),
                    payload=event_payload["data"],
                    run_id=run_id,
                    debug_input_data=input_data,
                )
                self._refresh_workflow_debug_state_from_single_node_payload(
                    app_id=app_id,
                    workflow_config=config,
                    node_id=node_id,
                    payload=event_payload["data"],
                    run_id=run_id,
                    messages=state.get("messages"),
                    variable_pool=variable_pool,
                )
                yield self._mask_runtime_secrets(event_payload, variable_pool)
                return

            async for item in node.execute_stream(state, variable_pool):
                if item.get("__final__"):
                    final_result = item["result"]
                else:
                    chunk = item.get("chunk", "")
                    if chunk:
                        yield self._mask_runtime_secrets(
                            {
                                "event": "node_chunk",
                                "data": {
                                    "node_id": node_id,
                                    "chunk": chunk,
                                    "execution_id": run_id,
                                }
                            },
                            variable_pool
                        )

            elapsed = (time.time() - start_time) * 1000
            extra_fields = node._extract_extra_fields(final_result)
            event_payload = {
                "event": "node_end",
                "data": self._attach_debug_execution_id(
                    {
                        "status": "completed",
                        "node_id": node_id,
                        "node_type": node_type,
                        "inputs": node._extract_input(state, variable_pool),
                        "outputs": node._extract_output(final_result),
                        "process": extra_fields.get("process"),
                        "agent_log": extra_fields.get("agent_log"),
                        "token_usage": node._extract_token_usage(final_result),
                        "elapsed_time": elapsed,
                        "error": None,
                    },
                    run_id,
                )
            }
            node_output = self._normalize_single_node_payload(node_type, node_config.get("name"), event_payload["data"])
            node._save_cache(
                state=state,
                variable_pool=variable_pool,
                node_output=node_output,
            )
            self._persist_single_node_execution(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                node_type=node_type,
                node_name=node_config.get("name"),
                payload=event_payload["data"],
                run_id=run_id,
                debug_input_data=input_data,
            )
            self._refresh_workflow_debug_state_from_single_node_payload(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                payload=event_payload["data"],
                run_id=run_id,
                messages=state.get("messages"),
                variable_pool=variable_pool,
            )
            yield self._mask_runtime_secrets(event_payload, variable_pool)
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"单节点流式执行失败: node_id={node_id}, error={e}", exc_info=True)
            event_payload = {
                "event": "node_error",
                "data": {
                    "execution_id": run_id,
                    "node_id": node_id,
                    "node_type": node_type,
                    "inputs": node._extract_input(state, variable_pool),
                    "elapsed_time": elapsed,
                    "error": str(e),
                }
            }
            self._persist_single_node_execution(
                app_id=app_id,
                workflow_config=config,
                node_id=node_id,
                node_type=node_type,
                node_name=node_config.get("name"),
                payload={
                    **event_payload["data"],
                    "status": "failed",
                    "outputs": None,
                    "process": None,
                    "token_usage": None,
                },
                run_id=run_id,
                debug_input_data=input_data,
            )
            yield self._mask_runtime_secrets(event_payload, variable_pool)

    @staticmethod
    def get_start_node_variables(config: dict) -> list:
        nodes = config.get("nodes", [])
        for node in nodes:
            if node.get("type") == NodeType.START:
                return node.get("config", {}).get("variables", [])
        return []

    @staticmethod
    def is_memory_enable(config: dict) -> bool:
        nodes = config.get("nodes", [])
        for node in nodes:
            if node.get("type") in [NodeType.MEMORY_READ, NodeType.MEMORY_WRITE]:
                return True
        return False

    @staticmethod
    def _validate_file_upload(
            features_config: dict[str, Any],
            files: Optional[list[FileInput]]
    ) -> None:
        """校验上传文件是否符合 file_upload 配置"""
        if not files:
            return
        fu = features_config.get("file_upload")
        if fu is None:
            return
        if not (isinstance(fu, dict) and fu.get("enabled")):
            raise BusinessException(
                "The application does not have file upload functionality enabled",
                BizCode.BAD_REQUEST
            )
        max_count = fu.get("max_file_count", 5)
        if len(files) > max_count:
            raise BusinessException(
                f"File count exceeds limit (maximum {max_count} files)",
                BizCode.BAD_REQUEST
            )

        # 校验传输方式
        allowed_methods = fu.get("allowed_transfer_methods", ["local_file", "remote_url"])
        for f in files:
            if f.transfer_method.value not in allowed_methods:
                raise BusinessException(
                    f"Unsupport file transfer method：{f.transfer_method.value},"
                    f"allowed method:{', '.join(allowed_methods)}",
                    BizCode.BAD_REQUEST
                )

        # 各类型对应的开关和大小限制配置键
        type_cfg = {
            "image": ("image_enabled", "image_max_size_mb", 20, "image"),
            "audio": ("audio_enabled", "audio_max_size_mb", 50, "audio"),
            "document": ("document_enabled", "document_max_size_mb", 100, "document"),
            "video": ("video_enabled", "video_max_size_mb", 500, "video"),
        }

        for f in files:
            ftype = str(f.type)  # 如 "image", "audio", "document", "video"
            cfg = type_cfg.get(ftype)
            if cfg is None:
                continue
            enabled_key, size_key, default_max_mb, label = cfg

            # 校验类型开关
            if not fu.get(enabled_key):
                raise BusinessException(
                    f"The application has not enabled {label} file upload",
                    BizCode.BAD_REQUEST
                )

            # 校验文件大小（仅当内容已加载时）
            content = f.get_content()
            if content is not None:
                max_mb = fu.get(size_key, default_max_mb)
                size_mb = len(content) / (1024 * 1024)
                if size_mb > max_mb:
                    raise BusinessException(
                        f"{label} File size exceeds the limit (maximum {max_mb} MB, current {size_mb:.1f} MB)",
                        BizCode.BAD_REQUEST
                    )


# ==================== 依赖注入函数 ====================

def get_workflow_service(
        db: Annotated[Session, Depends(get_db)]
) -> WorkflowService:
    """获取工作流服务（依赖注入）"""
    return WorkflowService(db)
