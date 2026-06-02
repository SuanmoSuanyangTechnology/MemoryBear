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
from app.core.workflow.validator import validate_workflow_config
from app.db import get_db
from app.models import App, AppRelease
from app.models.workflow_model import WorkflowConfig, WorkflowExecution
from app.repositories import knowledge_repository
from app.repositories.workflow_repository import (
    WorkflowConfigRepository,
    WorkflowExecutionRepository,
    WorkflowNodeExecutionRepository
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

    def __init__(self, db: Session):
        self.db = db
        self.config_repo = WorkflowConfigRepository(db)
        self.execution_repo = WorkflowExecutionRepository(db)
        self.node_execution_repo = WorkflowNodeExecutionRepository(db)
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
            "execution_config": execution_config or {},
            "features": features or {},
            "triggers": normalized_triggers,
            "workflow_type": workflow_type or "workflow",
            TRIGGER_NODES_PREPARED_FLAG: True,
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
            execution_config=execution_config if execution_config is not None else config.execution_config,
            features=features if features is not None else config.features,
            triggers=triggers if triggers is not None else config.triggers,
            workflow_type=workflow_type if workflow_type is not None else config.workflow_type,
        )
        updated_nodes = config_dict["nodes"]
        updated_edges = config_dict["edges"]
        updated_variables = config_dict["variables"]
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

    def get_execution_detail(self, execution_id: str) -> dict[str, Any] | None:
        execution = self.get_execution(execution_id)
        if not execution:
            return None

        node_executions = self.node_execution_repo.get_by_execution_id(execution.id)
        output_data = self._serialize_execution_value(execution.output_data or {})
        input_data = self._serialize_execution_value(execution.input_data or {})
        meta_data = self._serialize_execution_value(execution.meta_data or {})
        snapshot = {}
        if isinstance(output_data, dict):
            snapshot = output_data.get("snapshot") or {}

        return {
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
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config,
            "features": feature_configs
        }

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

                logger.info(f"Workflow Run Success, "
                            f"execution_id: {execution.execution_id}, message count: {len(final_messages)}")
            else:
                self.update_execution_status(
                    execution.execution_id,
                    "failed",
                    error_message=result.get("error")
                )
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
        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config,
            "features": feature_configs
        }

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
                elif event.get("event") == "workflow_start":
                    event["data"]["message_id"] = str(message_id)
                event = self._emit(public, event)
                if event:
                    yield event

                if event_type == "message" and output_moderation:
                    chunk = event_data.get("content", "")
                    output_moderation.accumulate(chunk)

                if event_type == "node_end" and output_moderation \
                        and event_data.get("node_id") in llm_node_ids \
                        and output_moderation.check_final():
                    yield {"event": "message_replace", "data": {"content": output_moderation.preset_response}}

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
        from app.core.workflow.variable.base_variable import VariableType

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

        # 如果目标节点是开始节点，将 inputs 中的变量值注入到 variables（sys.input_variables）
        if node_config.get("type") == NodeType.START:
            start_node_vars = node_config.get("config", {}).get("variables", [])
            start_node_id = node_config.get("id")
            inputs = input_data.get("inputs") or {}
            variables = input_data.get("variables") or {}
            for var_def in start_node_vars:
                var_name = var_def.get("name")
                # 优先匹配 start_node_id.var_name 格式，其次匹配裸 var_name
                keyed_value = inputs.get(f"{start_node_id}.{var_name}")
                bare_value = inputs.get(var_name)
                value = keyed_value if keyed_value is not None else bare_value
                if value is not None:
                    variables[var_name] = value
            # 解析开始节点文件类型的变量值
            variables = await self._resolve_start_node_file_variables(start_node_vars, variables)
            input_data["variables"] = variables

        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables or [],
            "execution_config": config.execution_config or {},
            "features": config.features or {},
        }

        storage_type, user_rag_memory_id = self._get_memory_store_info(workspace_id)
        execution_id = f"node_{uuid.uuid4().hex[:16]}"

        execution_context = ExecutionContext.create(
            execution_id=execution_id,
            workspace_id=str(workspace_id),
            user_id=input_data.get("user_id", ""),
            conversation_id=input_data.get("conversation_id", ""),
            memory_storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
        )

        # sys.files 转换为 FileObject 格式
        raw_files = input_data.get("files") or []
        if raw_files:
            from app.schemas.app_schema import FileInput
            file_inputs = [
                FileInput(**f) if isinstance(f, dict) else f
                for f in raw_files
            ]
            input_data["files"] = await self._handle_file_input(file_inputs)

        variable_pool = VariablePool()
        await VariablePoolInitializer(workflow_config_dict).initialize(variable_pool, input_data, execution_context)

        # 注入节点输入变量，支持扁平格式 {"node_id.var": value}
        for key, value in (input_data.get("inputs") or {}).items():
            if "." in key:
                ref_node_id, var_name = key.split(".", 1)
                var_type = VariableType.type_map(value)
                await variable_pool.new(ref_node_id, var_name, value, var_type, mut=False)

        cycle_nodes = [
            n.get("id") for n in workflow_config_dict.get("nodes", [])
            if n.get("type") in [NodeType.LOOP, NodeType.ITERATION]
        ]
        state = WorkflowState(
            messages=input_data.get("conv_messages", []),
            node_outputs={},
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
        start_time = time.time()
        try:
            result = await node.execute(state, variable_pool)
            elapsed = (time.time() - start_time) * 1000
            return {
                "status": "completed",
                "node_id": node_id,
                "node_type": node_config.get("type"),
                "inputs": node._extract_input(state, variable_pool),
                "outputs": node._extract_output(result),
                "process": node._extract_extra_fields(result).get("process"),
                "token_usage": node._extract_token_usage(result),
                "elapsed_time": elapsed,
                "error": None,
            }
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"单节点执行失败: node_id={node_id}, error={e}", exc_info=True)
            return {
                "status": "failed",
                "node_id": node_id,
                "node_type": node_config.get("type"),
                "inputs": node._extract_input(state, variable_pool),
                "outputs": None,
                "token_usage": None,
                "elapsed_time": elapsed,
                "error": str(e),
            }

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
        start_time = time.time()

        yield {"event": "node_start", "data": {"node_id": node_id, "node_type": node_type}}

        final_result = None
        try:
            async for item in node.execute_stream(state, variable_pool):
                if item.get("__final__"):
                    final_result = item["result"]
                else:
                    chunk = item.get("chunk", "")
                    if chunk:
                        yield {"event": "node_chunk", "data": {"node_id": node_id, "chunk": chunk}}

            elapsed = (time.time() - start_time) * 1000
            yield {
                "event": "node_end",
                "data": {
                    "node_id": node_id,
                    "node_type": node_type,
                    "status": "succeeded",
                    "inputs": node._extract_input(state, variable_pool),
                    "outputs": node._extract_output(final_result),
                    "process": node._extract_extra_fields(final_result).get("process"),
                    "token_usage": node._extract_token_usage(final_result),
                    "elapsed_time": elapsed,
                    "error": None,
                }
            }
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"单节点流式执行失败: node_id={node_id}, error={e}", exc_info=True)
            yield {
                "event": "node_error",
                "data": {
                    "node_id": node_id,
                    "node_type": node_type,
                    "inputs": node._extract_input(state, variable_pool),
                    "elapsed_time": elapsed,
                    "error": str(e),
                }
            }

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
