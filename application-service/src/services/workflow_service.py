"""
Workflow service layer.
"""
import datetime
import asyncio
import time
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Annotated, AsyncGenerator, Optional

import yaml
from fastapi import Depends
from sqlalchemy import delete, desc, insert, select, update
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..utils.datetime_utils import (
    as_utc_aware,
    parse_iso_to_utc_naive,
    to_iso_z,
    to_timestamp_ms,
    utcnow,
    utcnow_naive,
)
from ..workflow.node_cache import (
    WorkflowNodeCacheManager,
    normalize_cache_value,
    sanitize_json_text,
    sanitize_json_value,
)
from ..utils.workflow_triggers import (
    build_schedule_now_payload,
    get_trigger_type,
    is_trigger_enabled,
    iter_trigger_nodes,
    is_schedule_trigger_due,
    normalize_trigger_nodes,
    TRIGGER_NODES_PREPARED_FLAG,
)
from ..errors import BizCode
from ..config import current_settings
from ..errors import BusinessException
from ..usage_context import bind_usage
from ..workflow.adapters.registry import PlatformAdapterRegistry
from ..workflow.executor import execute_workflow, execute_workflow_stream
from ..utils.workflow_node_types import NodeType
from ..workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from ..utils.workflow_validator import validate_workflow_config
from ..aio_redis import aio_redis, aio_redis_delete, aio_redis_get, aio_redis_set, get_thread_safe_sync_redis
from ..db import get_async_db_context, get_async_pool_status, get_db
from ..models import App, AppRelease, Conversation, Message

if TYPE_CHECKING:
    from ..models import Message as MessageModel
from ..model_clients.embedding import RedBearEmbeddings
from ..models.workflow_model import WorkflowConfig, WorkflowExecution, WorkflowNodeCache, WorkflowNodeExecution
from ..repositories import knowledge_repository
from ..repositories.workflow_repository import (
    WorkflowConfigRepository,
    WorkflowExecutionRepository,
    WorkflowNodeExecutionRepository,
    WorkflowNodeCacheRepository,
)
from ..schemas import DraftRunRequest, FileInput, FileType, TransferMethod
from .annotation_service import AnnotationService
from .conversation_service import ConversationService
from .multi_agent_service import convert_uuids_to_str
from ..models.annotation_model import HitLogSource
from .multimodal_service import MultimodalService
from .workspace_service import get_workspace_storage_type_without_auth
from ..utils.redis_cache import (
    CACHE_MISS,
    delete_json,
    get_json,
    get_json_async,
    set_json,
    set_json_async,
    workflow_config_key,
)
from ..cache.workflow_cache import delete_workflow_config
from ..models.file_metadata_model import FileMetadata
from ..utils.file_processor import build_file_object_dict_from_meta, fetch_remote_file_meta

logger = logging.getLogger(__name__)

WORKFLOW_CONFIG_CACHE_TTL = 600


def _workflow_config_cache_safe(config: WorkflowConfig) -> bool:
    """Do not place workflow snapshots containing secret environment values in Redis."""
    return not any(
        item.get("value_type") == "secret"
        for item in (config.environment_variables or [])
        if isinstance(item, dict)
    )


def _serialize_workflow_config(config: WorkflowConfig) -> dict[str, Any]:
    return {
        "id": str(config.id),
        "app_id": str(config.app_id),
        "nodes": config.nodes or [],
        "edges": config.edges or [],
        "variables": config.variables or [],
        "environment_variables": config.environment_variables or [],
        "execution_config": config.execution_config or {},
        "features": config.features or {},
        "triggers": config.triggers or [],
        "workflow_type": config.workflow_type,
        "is_active": bool(config.is_active),
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _deserialize_workflow_config(payload: Any) -> WorkflowConfig | None:
    if not isinstance(payload, dict) or not payload.get("id") or not payload.get("app_id"):
        return None
    environment_variables = payload.get("environment_variables") or []
    if any(
        item.get("value_type") == "secret"
        for item in environment_variables
        if isinstance(item, dict)
    ):
        return None
    try:
        created_at = (
            datetime.datetime.fromisoformat(payload["created_at"])
            if payload.get("created_at") else utcnow_naive()
        )
        updated_at = (
            datetime.datetime.fromisoformat(payload["updated_at"])
            if payload.get("updated_at") else created_at
        )
        return WorkflowConfig(
            id=uuid.UUID(payload["id"]),
            app_id=uuid.UUID(payload["app_id"]),
            nodes=payload.get("nodes") or [],
            edges=payload.get("edges") or [],
            variables=payload.get("variables") or [],
            environment_variables=payload.get("environment_variables") or [],
            execution_config=payload.get("execution_config") or {},
            features=payload.get("features") or {},
            triggers=payload.get("triggers") or [],
            workflow_type=payload.get("workflow_type") or "workflow",
            is_active=bool(payload.get("is_active", True)),
            created_at=created_at,
            updated_at=updated_at,
        )
    except (TypeError, ValueError):
        logger.warning("Invalid workflow config cache payload", exc_info=True)
        return None


@dataclass
class WorkflowExecutionRef:
    id: uuid.UUID | None
    execution_id: str
    status: str
    conversation_id: uuid.UUID | None
    workflow_config_id: uuid.UUID | None
    app_id: uuid.UUID | None
    release_id: uuid.UUID | None
    trigger_type: str | None
    triggered_by: uuid.UUID | None
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    context: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    meta_data: dict[str, Any] | None
    error_message: str | None
    error_node_id: str | None
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    elapsed_time: float | None
    persisted: bool = False


class WorkflowService:


    DEBUG_STATE_NODE_ID = "__workflow_debug_state__"
    DEBUG_STATE_NODE_TYPE = "debug-state"
    DEBUG_STATE_NODE_NAME = "Workflow Debug State"
    DEBUG_STATE_SOURCE = "debug_state"
    # Node execution projections must persist normally; historical detail
    # remains sourced from WorkflowExecution.output_data.
    SKIP_EXECUTION_ARTIFACT_PERSIST_FOR_EXPERIMENT = False

    # Regeneration cap: max assistant versions per round (same parent_message_id),
    # including the original reply.
    MAX_REGENERATE_VERSIONS = 5

    def __init__(self, db: Session | AsyncSession | None = None):
        self.db = db
        # Sync repositories only work with a sync Session; for AsyncSession
        # (or no db) we use async DB contexts in the methods that need them.
        self._is_async_db = isinstance(db, AsyncSession)
        self.config_repo = WorkflowConfigRepository(db) if (db is not None and not self._is_async_db) else None
        self.execution_repo = WorkflowExecutionRepository(db) if (db is not None and not self._is_async_db) else None
        self.node_execution_repo = WorkflowNodeExecutionRepository(db) if (db is not None and not self._is_async_db) else None
        self.node_cache_repo = WorkflowNodeCacheRepository(db) if (db is not None and not self._is_async_db) else None
        self.conversation_service = ConversationService(db) if db is not None else None
        self.multimodal_service = MultimodalService(db) if db is not None else None

        self.registry = PlatformAdapterRegistry

    def _resolve_app_tenant_id(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        app = self.db.get(App, app_id)
        if not app:
            return None
        from ..repositories.tool_repository import ToolRepository
        return ToolRepository.get_tenant_id_by_workspace_id(self.db, str(app.workspace_id))

    async def _resolve_app_tenant_id_async(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        from ..models.workspace_model import Workspace

        async with get_async_db_context() as db:
            result = await db.execute(
                select(Workspace.tenant_id)
                .join(App, App.workspace_id == Workspace.id)
                .where(App.id == app_id)
                .limit(1)
            )
            return result.scalar_one_or_none()

    @staticmethod
    def _build_execution_ref(execution: WorkflowExecution) -> WorkflowExecutionRef:
        return WorkflowExecutionRef(
            id=execution.id,
            execution_id=execution.execution_id,
            status=execution.status,
            conversation_id=execution.conversation_id,
            workflow_config_id=execution.workflow_config_id,
            app_id=execution.app_id,
            release_id=execution.release_id,
            trigger_type=execution.trigger_type,
            triggered_by=execution.triggered_by,
            input_data=execution.input_data if isinstance(execution.input_data, dict) else None,
            output_data=execution.output_data if isinstance(execution.output_data, dict) else None,
            context=execution.context if isinstance(execution.context, dict) else None,
            token_usage=execution.token_usage if isinstance(execution.token_usage, dict) else None,
            meta_data=execution.meta_data if isinstance(execution.meta_data, dict) else None,
            error_message=execution.error_message,
            error_node_id=execution.error_node_id,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            elapsed_time=execution.elapsed_time,
            persisted=True,
        )

    @staticmethod
    def _runtime_execution_redis_key(execution_id: str) -> str:
        return f"workflow:runtime_execution:{execution_id}"

    @staticmethod
    def _serialize_runtime_execution_ref(execution: WorkflowExecutionRef) -> dict[str, Any]:
        return {
            "id": str(execution.id) if execution.id else None,
            "execution_id": execution.execution_id,
            "status": execution.status,
            "conversation_id": str(execution.conversation_id) if execution.conversation_id else None,
            "workflow_config_id": str(execution.workflow_config_id) if execution.workflow_config_id else None,
            "app_id": str(execution.app_id) if execution.app_id else None,
            "release_id": str(execution.release_id) if execution.release_id else None,
            "trigger_type": execution.trigger_type,
            "triggered_by": str(execution.triggered_by) if execution.triggered_by else None,
            "input_data": execution.input_data,
            "output_data": execution.output_data,
            "context": execution.context,
            "token_usage": execution.token_usage,
            "meta_data": execution.meta_data,
            "error_message": execution.error_message,
            "error_node_id": execution.error_node_id,
            "started_at": to_iso_z(execution.started_at) if execution.started_at else None,
            "completed_at": to_iso_z(execution.completed_at) if execution.completed_at else None,
            "elapsed_time": execution.elapsed_time,
            "persisted": execution.persisted,
        }

    @staticmethod
    def _runtime_execution_ref_from_snapshot(snapshot: dict[str, Any]) -> WorkflowExecutionRef | None:
        if not isinstance(snapshot, dict):
            return None

        def _uuid_or_none(value: Any) -> uuid.UUID | None:
            return uuid.UUID(value) if value else None

        def _dt_or_none(value: Any) -> datetime.datetime | None:
            return parse_iso_to_utc_naive(value) if value else None

        execution_id = snapshot.get("execution_id")
        if not execution_id:
            return None

        return WorkflowExecutionRef(
            id=_uuid_or_none(snapshot.get("id")),
            execution_id=execution_id,
            status=snapshot.get("status") or "running",
            conversation_id=_uuid_or_none(snapshot.get("conversation_id")),
            workflow_config_id=_uuid_or_none(snapshot.get("workflow_config_id")),
            app_id=_uuid_or_none(snapshot.get("app_id")),
            release_id=_uuid_or_none(snapshot.get("release_id")),
            trigger_type=snapshot.get("trigger_type"),
            triggered_by=_uuid_or_none(snapshot.get("triggered_by")),
            input_data=snapshot.get("input_data") if isinstance(snapshot.get("input_data"), dict) else None,
            output_data=snapshot.get("output_data") if isinstance(snapshot.get("output_data"), dict) else None,
            context=snapshot.get("context") if isinstance(snapshot.get("context"), dict) else None,
            token_usage=snapshot.get("token_usage") if isinstance(snapshot.get("token_usage"), dict) else None,
            meta_data=snapshot.get("meta_data") if isinstance(snapshot.get("meta_data"), dict) else None,
            error_message=snapshot.get("error_message"),
            error_node_id=snapshot.get("error_node_id"),
            started_at=_dt_or_none(snapshot.get("started_at")),
            completed_at=_dt_or_none(snapshot.get("completed_at")),
            elapsed_time=snapshot.get("elapsed_time"),
            persisted=bool(snapshot.get("persisted")),
        )

    @classmethod
    def _build_execution_record_from_ref(cls, execution: WorkflowExecutionRef) -> WorkflowExecution:
        return WorkflowExecution(
            id=execution.id or uuid.uuid4(),
            workflow_config_id=execution.workflow_config_id,
            release_id=execution.release_id,
            app_id=execution.app_id,
            conversation_id=execution.conversation_id,
            execution_id=execution.execution_id,
            trigger_type=execution.trigger_type or "manual",
            triggered_by=execution.triggered_by,
            input_data=execution.input_data,
            output_data=execution.output_data,
            context=execution.context or {},
            status=execution.status,
            error_message=execution.error_message,
            error_node_id=execution.error_node_id,
            started_at=execution.started_at or utcnow_naive(),
            completed_at=execution.completed_at,
            elapsed_time=execution.elapsed_time,
            token_usage=execution.token_usage,
            meta_data=execution.meta_data or {},
        )

    @classmethod
    def _build_transient_execution_ref(
            cls,
            *,
            workflow_config_id: uuid.UUID,
            app_id: uuid.UUID,
            trigger_type: str,
            release_id: uuid.UUID | None,
            triggered_by: uuid.UUID | None,
            conversation_id: uuid.UUID | None,
            input_data: dict[str, Any] | None,
            meta_data: dict[str, Any] | None,
            status: str,
    ) -> WorkflowExecutionRef:
        return WorkflowExecutionRef(
            id=None,
            execution_id=f"exec_{uuid.uuid4().hex[:16]}",
            status=status,
            conversation_id=conversation_id,
            workflow_config_id=workflow_config_id,
            app_id=app_id,
            release_id=release_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            input_data=input_data,
            output_data=None,
            context={},
            token_usage=None,
            meta_data=meta_data or {},
            error_message=None,
            error_node_id=None,
            started_at=utcnow_naive(),
            completed_at=None,
            elapsed_time=None,
            persisted=False,
        )

    async def _store_runtime_execution_snapshot_async(self, execution: WorkflowExecutionRef) -> None:
        await aio_redis_set(
            self._runtime_execution_redis_key(execution.execution_id),
            self._serialize_runtime_execution_ref(execution),
            expire=600,
        )

    async def _delete_runtime_execution_snapshot_async(self, execution_id: str) -> None:
        await aio_redis_delete(self._runtime_execution_redis_key(execution_id))

    async def _get_runtime_execution_snapshot_async(self, execution_id: str) -> WorkflowExecutionRef | None:
        raw_value = await aio_redis_get(self._runtime_execution_redis_key(execution_id))
        if not raw_value:
            return None
        try:
            import json
            return self._runtime_execution_ref_from_snapshot(json.loads(raw_value))
        except Exception as exc:
            logger.warning("Failed to decode runtime execution snapshot: execution_id=%s error=%s", execution_id, exc)
            return None

    def _get_runtime_execution_snapshot_sync(self, execution_id: str) -> WorkflowExecutionRef | None:
        try:
            client = get_thread_safe_sync_redis()
            raw_value = client.get(self._runtime_execution_redis_key(execution_id))
            if not raw_value:
                return None
            import json
            return self._runtime_execution_ref_from_snapshot(json.loads(raw_value))
        except Exception as exc:
            logger.warning("Failed to load runtime execution snapshot: execution_id=%s error=%s", execution_id, exc)
            return None

    @staticmethod
    def _build_runtime_workflow_config_from_release(
        release: AppRelease,
        real_config_id: uuid.UUID | None = None,
    ) -> WorkflowConfig:
        """Rebuild a runtime WorkflowConfig from a release snapshot."""
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
    def _build_persisted_execution_input_data(
        input_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(input_data, dict):
            return {}

        persisted: dict[str, Any] = {}
        for key in ("message", "conversation_id", "variables", "inputs", "conv"):
            value = input_data.get(key)
            if value is not None:
                persisted[key] = value

        trigger = input_data.get("trigger")
        if isinstance(trigger, dict):
            persisted["trigger"] = {
                "type": trigger.get("type"),
                "id": trigger.get("id"),
                "meta": trigger.get("meta") or {},
            }
            persisted["trigger_type"] = trigger.get("type") or "manual"
            persisted["trigger_id"] = trigger.get("id")
            persisted["trigger_meta"] = trigger.get("meta") or {}
        else:
            if input_data.get("trigger_type") is not None:
                persisted["trigger_type"] = input_data.get("trigger_type")
            if input_data.get("trigger_id") is not None:
                persisted["trigger_id"] = input_data.get("trigger_id")
            if input_data.get("trigger_meta") is not None:
                persisted["trigger_meta"] = input_data.get("trigger_meta")

        if input_data.get("trigger_payload") is not None:
            persisted["trigger_payload"] = input_data.get("trigger_payload")

        raw_files = input_data.get("files") or []
        if raw_files:
            persisted["files"] = [
                {
                    "type": file_item.get("type"),
                    "url": file_item.get("url"),
                    "file_type": file_item.get("file_type"),
                    "name": file_item.get("name"),
                    "size": file_item.get("size"),
                    "upload_file_id": file_item.get("upload_file_id") or file_item.get("file_id"),
                }
                for file_item in raw_files
                if isinstance(file_item, dict)
            ]

        return persisted

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
        current_time = utcnow()
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
            # trigger_payload already present; only fill in the missing trigger / meta
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
        config = self.config_repo.update_trigger_runtime(app_id, trigger_id, runtime)
        if config is not None:
            delete_json(workflow_config_key(app_id))
        return config

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
        nodes = config.get("nodes") or []
        updated = False
        new_nodes = []
        for node in nodes:
            # Fresh node dict instead of in-place mutation: an equal
            # assignment is skipped by SQLAlchemy and never persisted.
            if not updated and node.get("type") == NodeType.TRIGGER and node.get("id") == trigger_id:
                new_nodes.append({**node, "runtime": runtime})
                updated = True
            else:
                new_nodes.append(node)

        if not updated:
            return None

        config["nodes"] = new_nodes
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
        current_time = now or utcnow()
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
        current_time = now or utcnow()
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
        """Check whether the message hits an annotation.

        Args:
            app_id: Application ID
            message: User message
            source: Source (recorded as the hit source)
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

            from .model_service import ModelApiKeyService
            tenant_id = self._resolve_app_tenant_id(app_id)
            embedding_ref = ModelApiKeyService.resolve_invoke_ref(
                self.db,
                setting.model_config_id,
                tenant_id=tenant_id,
            )
            if not embedding_ref:
                return None

            return service.find_best_match(
                query=message,
                annotations=annotations,
                threshold=setting.similarity_threshold,
                embedding_ref=embedding_ref,
                app_id=app_id,
                source=source,
            )
        except Exception as e:
            logger.warning(f"标注匹配检查失败: {e}")
            return None

    async def _check_annotation_match_async(
            self,
            app_id: uuid.UUID,
            message: str,
            source: str = "",
    ) -> Optional[dict]:
        try:
            from ..models.annotation_model import AppAnnotation, AppAnnotationHitLog, AppAnnotationSetting
            from ..models.models_model import (
                ModelConfig,
                ModelProvider,
            )
            from .model_service import ModelApiKeyService
            from ..models.workspace_model import Workspace


            async with get_async_db_context() as db:
                # Read annotation settings
                result = await db.execute(
                    select(
                        AppAnnotationSetting.similarity_threshold,
                        AppAnnotationSetting.model_config_id,
                        AppAnnotationSetting.enabled,
                    )
                    .where(AppAnnotationSetting.app_id == app_id)
                    .limit(1)
                )
                setting_row = result.one_or_none()
                if not setting_row:
                    return None
                similarity_threshold, model_config_id, enabled = setting_row
                if not enabled or not model_config_id:
                    return None

                # Read annotations + model config in same session
                result = await db.execute(
                    select(
                        AppAnnotation.id,
                        AppAnnotation.question,
                        AppAnnotation.answer,
                        AppAnnotation.embedding,
                        AppAnnotation.hit_count,
                    ).where(
                        AppAnnotation.app_id == app_id,
                        AppAnnotation.is_active == 1,
                    )
                )
                annotations = list(result.mappings())
                if not annotations:
                    return None

                result = await db.execute(
                    select(ModelConfig)
                    .where(ModelConfig.id == model_config_id)
                    .limit(1)
                )
                model_cfg = result.scalar_one_or_none()
                if not model_cfg or not model_cfg.is_active:
                    return None

                # Public speedbear configs belong to the system tenant; resolve the
                # channel by the tenant of the app
                tenant_id = None
                if model_cfg.provider == ModelProvider.SPEEDBEAR and model_cfg.is_public:
                    result = await db.execute(
                        select(Workspace.tenant_id)
                        .join(App, App.workspace_id == Workspace.id)
                        .where(App.id == app_id)
                        .limit(1)
                    )
                    tenant_id = result.scalar_one_or_none()
                    if not tenant_id:
                        return None

                embedding_ref = await ModelApiKeyService.resolve_invoke_ref_async(
                    db,
                    model_cfg.id,
                    tenant_id=tenant_id,
                )
                if not embedding_ref:
                    return None

            query_embedding = await RedBearEmbeddings.for_invoke(embedding_ref).aembed_query(message)
            best_match = None
            best_similarity = 0.0
            for annotation in annotations:
                embedding = annotation["embedding"]
                if not embedding:
                    continue
                similarity = AnnotationService.cosine_similarity(query_embedding, embedding)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = annotation

            if not best_match or best_similarity < float(similarity_threshold):
                return None

            async with get_async_db_context() as db:
                await db.execute(
                    update(AppAnnotation)
                    .where(AppAnnotation.id == best_match["id"])
                    .values(hit_count=int(best_match["hit_count"] or 0) + 1)
                )
                db.add(
                    AppAnnotationHitLog(
                        annotation_id=best_match["id"],
                        app_id=app_id,
                        source=source,
                        query=message,
                        matched_question=best_match["question"],
                        answer=best_match["answer"],
                        similarity=best_similarity,
                    )
                )
                await db.commit()
                return {
                    "annotation_id": str(best_match["id"]),
                    "question": best_match["question"],
                    "answer": best_match["answer"],
                    "similarity": best_similarity,
                }
        except Exception as e:
            logger.warning(f"标注匹配检查失败: {e}")
            return None

    # ==================== Configuration management ====================

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
            validate: bool = True,
            *,
            redis_client: Any
    ) -> WorkflowConfig:
        """Create a workflow config.

        Args:
            app_id: Application ID
            nodes: Node list
            edges: Edge list
            variables: Variable list
            execution_config: Execution config
            features: Feature flags
            triggers: Trigger list
            workflow_type: Workflow type
            validate: Whether to validate the config

        Returns:
            The workflow config

        Raises:
            BusinessException: Raised when the config is invalid
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
            is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
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
        """Get the workflow config (ORM; intended for modification paths)."""
        return self.config_repo.get_by_app_id(app_id)

    def get_workflow_config_snapshot(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """Return a transient WorkflowConfig reconstructed from a JSON cache snapshot."""
        cache_key = workflow_config_key(app_id)
        cached = _deserialize_workflow_config(get_json(cache_key))
        if cached is not None:
            return cached

        config = self.config_repo.get_by_app_id(app_id)
        if config and _workflow_config_cache_safe(config):
            set_json(cache_key, _serialize_workflow_config(config), WORKFLOW_CONFIG_CACHE_TTL)
        return config

    async def get_workflow_config_async(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """Fetch the workflow config asynchronously (async ORM entry point)."""
        async with get_async_db_context() as db:
            return await WorkflowConfigRepository(db).get_by_app_id_async(app_id)

    async def get_workflow_config_snapshot_async(self, app_id: uuid.UUID) -> WorkflowConfig | None:
        """Async JSON cache-aside lookup for read-only workflow execution."""
        cache_key = workflow_config_key(app_id)
        cached_payload = await get_json_async(cache_key)
        if cached_payload is not CACHE_MISS:
            cached = _deserialize_workflow_config(cached_payload)
            if cached is not None:
                return cached

        cache_payload = None
        async with get_async_db_context() as db:
            config = await WorkflowConfigRepository(db).get_by_app_id_async(app_id)
            if config is not None:
                # The context manager rolls back read transactions on exit, which
                # expires session-bound attributes. Materialize every field and
                # detach the snapshot before that rollback.
                payload = _serialize_workflow_config(config)
                if _workflow_config_cache_safe(config):
                    cache_payload = payload
                db.expunge(config)

        if cache_payload is not None:
            await set_json_async(
                cache_key,
                cache_payload,
                WORKFLOW_CONFIG_CACHE_TTL,
            )
        return config

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
        """Update a workflow config.

        Args:
            app_id: Application ID
            nodes: Node list
            edges: Edge list
            variables: Variable list
            execution_config: Execution config
            features: Feature flags
            triggers: Trigger list
            workflow_type: Workflow type
            validate: Whether to validate the config

        Returns:
            The workflow config

        Raises:
            BusinessException: Raised when the config is missing or invalid
        """
        # Load the existing config
        config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException(
                code=BizCode.NOT_FOUND,
                message=f"工作流配置不存在: app_id={app_id}"
            )

        # Merge the config
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

        # Validate the config
        if validate:
            is_valid, errors = validate_workflow_config(config_dict, for_publish=False)
            if not is_valid:
                logger.warning(f"工作流配置验证失败: {errors}")
                raise BusinessException(
                    code=BizCode.INVALID_PARAMETER,
                    message=f"工作流配置无效: {'; '.join(errors)}"
                )

        # Persist the updated config
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
        delete_json(workflow_config_key(app_id))

        logger.info(f"更新工作流配置成功: app_id={app_id}, config_id={config.id}")
        return config

    def delete_workflow_config(self, app_id: uuid.UUID) -> bool:
        """Delete a workflow config.

        Args:
            app_id: Application ID

        Returns:
            Whether the deletion succeeded
        """
        config = self.get_workflow_config(app_id)
        if not config:
            return False
        config.is_active = False
        self.db.commit()
        delete_json(workflow_config_key(app_id))
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
        """Check the integrity of the workflow config.

        Args:
            app_id: Application ID

        Raises:
            BusinessException: Raised when the config is incomplete or missing
        """

        # 1. Check whether a multi-agent config exists
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
        """Validate whether the workflow config can be published.

        Args:
            app_id: Application ID

        Returns:
            (is_valid, errors): Validity flag and error list

        Raises:
            BusinessException: Raised when the config is missing
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

    # ==================== Execution management ====================

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
        """Create a workflow execution record.

        Args:
            release_id: App release ID
            workflow_config_id: Workflow config ID
            app_id: Application ID
            trigger_type: Trigger type
            triggered_by: Triggering user ID
            conversation_id: Conversation ID
            input_data: Input data

        Returns:
            The execution record
        """
        # Generate the execution ID
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
        """Get an execution record.

        Args:
            execution_id: Execution ID

        Returns:
            The execution record, or None
        """
        execution = self.execution_repo.get_by_execution_id(execution_id)
        if execution:
            return execution
        runtime_execution = self._get_runtime_execution_snapshot_sync(execution_id)
        if runtime_execution:
            return self._build_execution_record_from_ref(runtime_execution)
        return None

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
    def _get_execution_source(execution: WorkflowExecution | WorkflowExecutionRef | None) -> str:
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

    # Keys in output_data["node_outputs"][node_id] that carry node metadata.
    # Only the remainder after stripping these keys holds node output variables
    # (matching the value rules of _build_node_execution_record).
    _NODE_OUTPUT_META_KEYS = frozenset({
        "node_type",
        "node_name",
        "status",
        "error",
        "input",
        "output",
        "execution_order",
        "retry_count",
        "elapsed_time",
        "token_usage",
        "process",
        "agent_log",
        "cache_hit",
        "cache_key",
    })

    @classmethod
    def _extract_node_output_value(cls, node_data: Any) -> Any:
        """Extract the node output value from output_data["node_outputs"][node_id].

        Value rules match _build_node_execution_record(): take the value of the
        "output" key when present, otherwise the remainder after stripping metadata keys.

        Returns:
            The node output value; None means no usable value is available
            (callers must keep their existing placeholder logic).
        """
        if not isinstance(node_data, dict):
            return node_data
        if "output" in node_data:
            return node_data.get("output")
        remainder = {
            key: value
            for key, value in node_data.items()
            if key not in cls._NODE_OUTPUT_META_KEYS
        }
        return remainder or None

    @classmethod
    def _build_ordered_node_snapshots(
            cls,
            *,
            node_executions: list[WorkflowNodeExecution],
            raw_node_vars: Any,
            node_type_maps: dict[str, dict[str, str]],
            node_outputs: Any = None,
    ) -> dict[str, Any]:
        """Assemble the nodes group of the snapshot.

        Value priority:
            output_data.snapshot.nodes[node_id]
              → workflow_node_executions.output_data
                → output_data.node_outputs[node_id].output
                  → placeholder value derived from the type definition

        The node_outputs fallback covers historical executions whose node rows were
        purged under the retention policy: node_executions is empty there, so node
        order and values are rebuilt from the parent execution's node_outputs.
        """
        ordered_node_vars: dict[str, Any] = {}
        serialized_raw_node_vars = cls._serialize_execution_value(raw_node_vars or {})
        serialized_node_outputs = cls._serialize_execution_value(node_outputs or {})
        if not isinstance(serialized_node_outputs, dict):
            serialized_node_outputs = {}

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
            if node_execution.output_data:
                serialized_output = cls._serialize_execution_value(node_execution.output_data)
                if isinstance(serialized_output, dict) and "output" in serialized_output:
                    serialized_output = serialized_output.get("output")
            else:
                # When the node row exists but output_data is empty, fall back to
                # node_outputs; if both are absent, keep the existing empty-group
                # behavior and let the placeholder fill below handle it.
                fallback_output = cls._extract_node_output_value(
                    serialized_node_outputs.get(node_id)
                )
                serialized_output = fallback_output if fallback_output is not None else {}
            ordered_node_vars[node_id] = cls._build_typed_node_snapshot(
                serialized_output,
                type_map=node_type_map,
            )

        # When node rows were purged under the retention policy, rebuild order and
        # values from node_outputs sorted by execution_order
        for node_id, node_data in sorted(
                serialized_node_outputs.items(),
                key=cls._node_output_sort_key,
        ):
            if node_id in ordered_node_vars:
                continue
            node_type_map = node_type_maps.get(node_id)
            if isinstance(serialized_raw_node_vars, dict) and node_id in serialized_raw_node_vars:
                ordered_node_vars[node_id] = cls._build_typed_node_snapshot(
                    serialized_raw_node_vars[node_id],
                    type_map=node_type_map,
                )
                continue
            fallback_output = cls._extract_node_output_value(node_data)
            if fallback_output is None:
                continue
            ordered_node_vars[node_id] = cls._build_typed_node_snapshot(
                fallback_output,
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
            node_outputs=output_data.get("node_outputs") if isinstance(output_data, dict) else None,
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
            execution: WorkflowExecution | WorkflowExecutionRef,
            node_executions: list[WorkflowNodeExecution],
            output_data: dict[str, Any],
            workflow_config: "WorkflowConfig | None" = None,
    ) -> dict[str, Any]:
        workflow_config = workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
        type_maps = self._build_snapshot_type_maps(workflow_config)
        raw_groups = self._extract_execution_snapshot_raw_groups(
            output_data=output_data,
            workflow_config=workflow_config,
        )
        ordered_node_vars = self._build_ordered_node_snapshots(
            node_executions=node_executions,
            raw_node_vars=raw_groups["nodes"],
            node_type_maps=type_maps["nodes"],
            node_outputs=output_data.get("node_outputs") if isinstance(output_data, dict) else None,
        )
        return {
            "conversation": self._normalize_typed_group(
                raw_groups["conversation"],
                type_map=type_maps["conversation"],
            ),
            "nodes": ordered_node_vars,
        }

    def _build_execution_snapshot_from_record(self, execution: WorkflowExecution) -> dict[str, Any]:
        node_executions = self.node_execution_repo.get_by_execution_id(execution.id) if execution.id else []
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
            var_type = item.get("type")
            default = item.get("default")
            if var_type == VariableType.FILE.value:
                value = default if isinstance(default, dict) and default.get("is_file") else None
            elif var_type == VariableType.ARRAY_FILE.value:
                value = [v for v in (default or []) if isinstance(v, dict) and v.get("is_file")] if default else []
            else:
                value = default
            result[name] = self._build_typed_variable(value, var_type)
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
        state_cache = manager.get_latest_cache_sync(include_inactive=False)
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
        saved_cache = manager.save_cache_sync(
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

    def _refresh_workflow_debug_state_from_execution(self, execution: WorkflowExecution, workflow_config: WorkflowConfig | None = None) -> None:
        workflow_config = workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
        node_executions = self.node_execution_repo.get_by_execution_id(execution.id)
        output_data = self._serialize_execution_value(execution.output_data or {})
        snapshot = self._build_public_execution_snapshot_record(
            execution=execution,
            node_executions=node_executions,
            output_data=output_data if isinstance(output_data, dict) else {},
            workflow_config=workflow_config,
        )
        self._write_workflow_debug_state(
            app_id=execution.app_id,
            workflow_config=workflow_config,
            snapshot=snapshot or self._build_default_debug_state_snapshot(workflow_config),
            messages=self._extract_execution_messages(execution),
            execution_id=execution.execution_id,
            source=self._get_execution_source(execution),
        )

    def _extract_execution_messages(self, execution: WorkflowExecution | WorkflowExecutionRef | None) -> list[dict[str, Any]]:
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
        from ..schemas.app_schema import FileInput
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
            if resolved_type == VariableType.FILE:
                if not isinstance(resolved_value, dict) or not resolved_value.get("is_file"):
                    resolved_value = None
            elif resolved_type == VariableType.ARRAY_FILE:
                if not isinstance(resolved_value, list):
                    resolved_value = []
                else:
                    resolved_value = [v for v in resolved_value if isinstance(v, dict) and v.get("is_file")]
            elif resolved_value is None:
                if resolved_type != VariableType.ANY:
                    resolved_value = DEFAULT_VALUE(resolved_type)
            await variable_pool.new(
                namespace=namespace,
                key=name,
                value=resolved_value,
                var_type=resolved_type,
                mut=mut,
            )

    async def _apply_start_node_cache_to_variable_pool(
            self,
            *,
            variable_pool,
            runtime_snapshot: dict[str, Any] | None,
            workflow_config: WorkflowConfig | None,
    ) -> None:
        """Promote start-node cached output fields into sys.* variables.

        When the user edits the start node's output in the debug panel
        (e.g. changes 'message' to '你是谁'), the value is stored in
        debug_snapshot.nodes[start_node_id].  This method reads that snapshot
        and writes the relevant fields back to sys.* so that rerun of any
        downstream node sees the overridden values.
        """
        if not isinstance(runtime_snapshot, dict):
            return
        nodes_snapshot = runtime_snapshot.get("nodes") or {}
        if not isinstance(nodes_snapshot, dict) or not nodes_snapshot:
            return
        # Find the start node id from workflow config
        start_node_id = next(
            (n.get("id") for n in (getattr(workflow_config, "nodes", None) or []) if n.get("type") == NodeType.START),
            None,
        )
        if not start_node_id or start_node_id not in nodes_snapshot:
            return
        start_output = self._unwrap_typed_group(nodes_snapshot[start_node_id])
        if not isinstance(start_output, dict):
            return
        sys_field_map = {
            "message": VariableType.STRING,
            "files": VariableType.ARRAY_FILE,
        }
        for field, var_type in sys_field_map.items():
            if field in start_output and start_output[field] is not None:
                await variable_pool.new("sys", field, start_output[field], var_type, mut=False)

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
    def _mask_runtime_secrets(payload: dict[str, Any], _variable_pool) -> dict[str, Any]:
        return payload

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
    def _mask_payload_with_secret_values(payload: dict[str, Any], _secret_values: list[str]) -> dict[str, Any]:
        return payload

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
        process = node_output.get("process")
        status = (
            "preview"
            if isinstance(process, dict) and process.get("preview")
            else node_output.get("status", "completed")
        )
        return {
            "status": status,
            "node_id": node_id,
            "node_type": node_type,
            "inputs": node_output.get("input"),
            "outputs": node_output.get("output"),
            "process": process,
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
        # Node outputs (e.g. raw chunk text returned by knowledge retrieval) may
        # contain NUL characters that jsonb columns cannot store; strip them before
        # persisting to avoid asyncpg UntranslatableCharacterError (22P05).
        normalized = sanitize_json_value(dict(node_data or {}))
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

    def _build_node_executions_from_output_data(
            self,
            output_data: dict[str, Any],
            *,
            execution: WorkflowExecution,
            workflow_config: WorkflowConfig | None = None,
    ) -> list[dict[str, Any]]:
        """Rebuild node details for execution records from workflow_executions.output_data["node_outputs"].

        Under the retention policy, workflow_node_executions keeps only the latest
        execution per workflow, so reading the node table directly for historical
        executions yields an empty list; rebuild from the parent execution's
        node_outputs as a fallback instead.

        Reuses the field mapping rules of _build_node_execution_record() (the same
        rules used when writing node rows) so the output stays field-for-field
        isomorphic with a direct node-table read and the two parsers cannot drift.

        Args:
            output_data: Serialized parent execution output_data
            execution: Parent execution record (provides baseline values such as
                started_at / completed_at / app_id)
            workflow_config: Workflow config, used to fill in node_name

        Returns:
            List of dicts isomorphic with the node_executions elements of
            get_execution_detail (sorted by execution_order)
        """
        node_outputs = output_data.get("node_outputs") if isinstance(output_data, dict) else None
        if not isinstance(node_outputs, dict) or not node_outputs:
            return []

        ordered_node_outputs = sorted(node_outputs.items(), key=self._node_output_sort_key)
        payload: list[dict[str, Any]] = []
        for index, (node_id, node_data) in enumerate(ordered_node_outputs, start=1):
            if not isinstance(node_data, dict):
                continue
            record = self._build_node_execution_record(
                execution=execution,
                node_id=node_id,
                node_data=node_data,
                source="workflow_execution",
                fallback_execution_order=index,
                fallback_node_name=self._get_node_name_from_config(workflow_config, node_id),
            )
            record_meta_data = record.get("meta_data") or {}
            payload.append({
                "node_id": record["node_id"],
                "node_type": record["node_type"],
                "node_name": record["node_name"],
                "execution_order": record["execution_order"],
                "retry_count": record["retry_count"],
                "status": record["status"],
                "input_data": self._serialize_execution_value(record["input_data"] or {}),
                "output_data": self._serialize_execution_value(record["output_data"] or {}),
                "agent_log": self._serialize_execution_value(record_meta_data.get("agent_log")),
                "error_message": record["error_message"],
                "started_at": to_iso_z(record["started_at"]),
                "completed_at": to_iso_z(record["completed_at"]),
                "elapsed_time": record["elapsed_time"],
                "token_usage": self._serialize_execution_value(record["token_usage"]),
                "cache_hit": record["cache_hit"],
                "cache_key": record["cache_key"],
                "meta_data": self._serialize_execution_value(record_meta_data),
            })
        return payload

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
        if current_settings().workflow_node_execution_retention_enabled:
            # After a successful write, keep only the node rows of the latest execution
            # per workflow, keyed by execution start time.
            self.db.flush()
            self.node_execution_repo.delete_stale_workflow_executions(
                app_id=execution.app_id,
                workflow_config_id=execution.workflow_config_id,
                keep_execution_id=execution.id,
            )
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
        # As above: single-node debug payloads also land in jsonb columns; strip NUL first.
        normalized_payload = sanitize_json_value(
            self._normalize_single_node_payload(node_type, node_name, payload)
        )
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
        if current_settings().workflow_node_execution_retention_enabled:
            # Flush first to obtain the new row id, then purge historical debug
            # rows for the same node: old records are removed only after the new
            # write succeeds, so a failure cannot take out the previous debug record.
            self.db.flush()
            removed = self.node_execution_repo.delete_stale_single_node_debug(
                app_id=app_id,
                workflow_config_id=workflow_config.id,
                node_id=node_id,
                keep_id=node_execution.id,
            )
            if removed:
                logger.debug(
                    "single node debug retention removed %s stale rows (app_id=%s node_id=%s)",
                    removed,
                    app_id,
                    node_id,
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
        cache = manager.get_latest_cache_sync(include_inactive=False)
        secret_values = self._extract_secret_values_from_environment_variables(
            config.environment_variables if config else []
        )
        serialized = self._serialize_node_cache(cache)
        return self._mask_payload_with_secret_values(serialized, secret_values) if serialized else None

    def _update_conversation_variable_cache(
            self,
            *,
            app_id: uuid.UUID,
            config,
            result_data: dict[str, Any] | None = None,
            patches: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Write conversation variables into debug_state.snapshot.conversation so they take effect on rerun."""
        debug_state = self._read_workflow_debug_state(app_id=app_id, workflow_config=config)
        debug_snapshot = dict(debug_state["snapshot"])
        existing_conv = dict(debug_snapshot.get("conversation") or {})

        if patches:
            patch_base = self._unwrap_typed_group(existing_conv)
            # Conversation variables are flat: patch name/path maps directly to the
            # variable name, with no scope layer
            type_map = self._build_snapshot_type_maps(config)["conversation"]
            next_raw = dict(patch_base)
            for patch in patches:
                var_name = patch.get("name") or patch.get("path")
                if not var_name:
                    continue
                next_raw[var_name] = patch.get("value")
            new_conv_snapshot = self._normalize_typed_group(next_raw, type_map=type_map)
        else:
            # result_data is already in typed format; use it directly, filtering out
            # fields that are not known conversation variables
            known_vars = {item.get("name") for item in (config.variables or []) if item.get("name")}
            new_conv_snapshot = {
                k: normalize_cache_value(v)
                for k, v in (result_data or {}).items()
                if k in known_vars
            }

        merged_conv = self._merge_typed_group(existing_conv, new_conv_snapshot)
        debug_snapshot["conversation"] = merged_conv
        self._write_workflow_debug_state(
            app_id=app_id,
            workflow_config=config,
            snapshot=debug_snapshot,
            messages=debug_state.get("messages"),
            execution_id=debug_state.get("execution_id"),
            source="cache_update",
        )
        return {
            "node_id": "conversation",
            "node_type": "conversation",
            "result_data": merged_conv,
        }

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
        if node_id == "conversation":
            return self._update_conversation_variable_cache(
                app_id=app_id, config=config, result_data=result_data, patches=patches
            )
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
        latest_cache = manager.get_latest_cache_sync(include_inactive=False)
        next_result_data = self._sanitize_cache_result_data(result_data or {})
        if patches:
            if latest_cache:
                patch_base = latest_cache.get("result_data") or {}
            else:
                debug_state = self._read_workflow_debug_state(
                    app_id=app_id, workflow_config=config
                )
                debug_nodes = (debug_state.get("snapshot") or {}).get("nodes") or {}
                patch_base = self._unwrap_typed_group(debug_nodes.get(node_id) or {})
            next_result_data = self._apply_cache_result_patches(patch_base, patches)
        if not latest_cache:
            cache_key = manager.build_cache_key(next_result_data)
            updated = manager.save_cache_sync(
                cache_key=cache_key,
                input_data={},
                result_data=next_result_data,
                source="manual",
                ttl_seconds=None,
            )
        else:
            updated = manager.update_latest_cache_sync(
                result_data=next_result_data,
            )
        if not updated:
            return None
        node_type_maps = self._build_snapshot_type_maps(config)["nodes"]
        # Build the new snapshot for this node from the patched result_data.
        # Then merge it with the existing snapshot so fields not covered by
        # the patch (e.g. user_id, workspace_id on the start node) are kept.
        new_node_snapshot = self._build_public_node_snapshot_from_cache_result(
            next_result_data,
            type_map=node_type_maps.get(node_id),
        )
        existing_debug_state = self._read_workflow_debug_state(
            app_id=app_id, workflow_config=config
        )
        existing_node_snapshot = (
            existing_debug_state.get("snapshot") or {}
        ).get("nodes", {}).get(node_id) or {}
        merged_node_snapshot = self._merge_typed_group(existing_node_snapshot, new_node_snapshot)
        self._sync_workflow_debug_state_node(
            app_id=app_id,
            workflow_config=config,
            node_id=node_id,
            node_snapshot=merged_node_snapshot,
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
        affected = manager.invalidate_latest_cache_sync()
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
        # The source filter is required: this method reruns from the latest
        # single-node debug input, and without the filter it would pick up node
        # rows from the latest full workflow execution, diverging from the
        # check in _has_single_node_debug_input.
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
            debug_input_data = node_execution.input_data if isinstance(node_execution.input_data, dict) else {}
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
            prefer_cached_runtime=True,
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
        workflow_config = execution.workflow_config or self.db.get(WorkflowConfig, execution.workflow_config_id)
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
            "node_executions": (
                [
                    {
                        "node_id": node_execution.node_id,
                        "node_type": node_execution.node_type,
                        "node_name": node_execution.node_name,
                        "execution_order": node_execution.execution_order,
                        "retry_count": node_execution.retry_count,
                        "status": node_execution.status,
                        "input_data": self._serialize_execution_value(node_execution.input_data or {}),
                        "output_data": self._serialize_execution_value(node_execution.output_data or {}),
                        "agent_log": self._serialize_execution_value(
                            (node_execution.meta_data or {}).get("agent_log")
                        ),
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
                ]
                if node_executions
                # The node table keeps only the latest execution under the
                # retention policy; rebuild historical executions from the parent
                # execution's output_data["node_outputs"] so node details stay non-empty.
                else self._build_node_executions_from_output_data(
                    output_data,
                    execution=execution,
                    workflow_config=workflow_config,
                )
            ),
        }
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
        """List execution records for an app.

        Args:
            app_id: Application ID
            limit: Max number of records to return
            offset: Offset

        Returns:
            Execution record list
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
        """Update the execution status.

        Args:
            execution_id: Execution ID
            status: Status
            token_usage: Token usage
            output_data: Output data
            error_message: Error message
            error_node_id: ID of the node that failed

        Returns:
            The execution record

        Raises:
            BusinessException: Raised when the execution record is missing
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

        # Compute elapsed time when reaching a terminal status
        if status in ["completed", "failed", "cancelled", "timeout"]:
            if not execution.completed_at:
                execution.completed_at = utcnow_naive()
                elapsed = (execution.completed_at - execution.started_at).total_seconds()
                execution.elapsed_time = elapsed

        self.db.commit()
        self.db.refresh(execution)

        logger.info(f"更新执行状态: execution_id={execution_id}, status={status}")
        return execution

    async def _create_execution_async(
            self,
            workflow_config_id: uuid.UUID,
            app_id: uuid.UUID,
            trigger_type: str,
            release_id: uuid.UUID | None = None,
            triggered_by: uuid.UUID | None = None,
            conversation_id: uuid.UUID | None = None,
            input_data: dict[str, Any] | None = None,
            meta_data: dict[str, Any] | None = None,
            status: str = "pending",
    ) -> WorkflowExecutionRef:
        execution_id = f"exec_{uuid.uuid4().hex[:16]}"
        started_at = time.perf_counter()
        async with get_async_db_context() as db:
            after_session_opened_at = time.perf_counter()
            logger.info(
                "[TIMING] workflow.create_execution stage=session_opened execution_id=%s "
                "total_ms=%.2f status=%s",
                execution_id,
                (after_session_opened_at - started_at) * 1000,
                status,
            )
            execution = WorkflowExecution(
                workflow_config_id=workflow_config_id,
                app_id=app_id,
                release_id=release_id,
                conversation_id=conversation_id,
                execution_id=execution_id,
                trigger_type=trigger_type,
                triggered_by=triggered_by,
                input_data=self._build_persisted_execution_input_data(input_data),
                meta_data=meta_data or {},
                status=status,
            )
            before_add_at = time.perf_counter()
            db.add(execution)
            before_commit_at = time.perf_counter()
            logger.info(
                "[TIMING] workflow.create_execution stage=before_commit execution_id=%s "
                "total_ms=%.2f add_ms=%.2f status=%s",
                execution_id,
                (before_commit_at - started_at) * 1000,
                (before_commit_at - before_add_at) * 1000,
                status,
            )
            await db.commit()
            after_commit_at = time.perf_counter()
            pool_status = get_async_pool_status()
            logger.info(
                "[TIMING] workflow.create_execution stage=after_commit execution_id=%s "
                "total_ms=%.2f commit_ms=%.2f status=%s pool_checked_out=%s "
                "pool_overflow=%s pool_usage_percent=%s",
                execution_id,
                (after_commit_at - started_at) * 1000,
                (after_commit_at - before_commit_at) * 1000,
                status,
                pool_status["checked_out"],
                pool_status["overflow"],
                pool_status["usage_percent"],
            )
            logger.info(f"创建工作流执行记录: execution_id={execution_id}")
            logger.info(
                f">>>>>>> TIMING_TRACE execution_started execution_id={execution_id} "
                f"started_at={execution.started_at.isoformat() if execution.started_at else 'None'}"
            )
            return self._build_execution_ref(execution)

    async def _get_execution_async(self, execution_id: str) -> WorkflowExecutionRef | None:
        async with get_async_db_context() as db:
            result = await db.execute(
                select(WorkflowExecution).where(WorkflowExecution.execution_id == execution_id)
            )
            execution = result.scalar_one_or_none()
            if execution:
                return self._build_execution_ref(execution)
            runtime_execution = await self._get_runtime_execution_snapshot_async(execution_id)
            return runtime_execution

    async def _patch_execution_async(
            self,
            execution_id: str,
            **fields: Any,
    ) -> WorkflowExecutionRef:
        async with get_async_db_context() as db:
            started_at = time.perf_counter()
            result = await db.execute(
                select(WorkflowExecution).where(WorkflowExecution.execution_id == execution_id)
            )
            execution = result.scalar_one_or_none()
            if not execution:
                runtime_execution = await self._get_runtime_execution_snapshot_async(execution_id)
                if not runtime_execution:
                    raise BusinessException(
                        code=BizCode.NOT_FOUND,
                        message=f"执行记录不存在: execution_id={execution_id}",
                    )
                execution = self._build_execution_record_from_ref(runtime_execution)
                db.add(execution)

            for key, value in fields.items():
                if value is None and key not in {"error_message", "error_node_id"}:
                    continue
                if key == "output_data" and value is not None:
                    value = convert_uuids_to_str(value)
                elif key == "error_message" and isinstance(value, str):
                    # Node errors may embed raw chunk text (with NUL bytes);
                    # text columns cannot store that either.
                    value = sanitize_json_text(value)
                setattr(execution, key, value)

            status = fields.get("status")
            if status in {"completed", "failed", "cancelled", "timeout"} and not execution.completed_at:
                execution.completed_at = utcnow_naive()
                execution.elapsed_time = (
                    (execution.completed_at - execution.started_at).total_seconds()
                    if execution.started_at else 0.0
                )
                logger.info(
                    f">>>>>>> TIMING_TRACE execution_completed execution_id={execution_id} "
                    f"status={status} "
                    f"started_at={execution.started_at.isoformat() if execution.started_at else 'None'} "
                    f"completed_at={execution.completed_at.isoformat()}"
                )

            before_commit_at = time.perf_counter()
            await db.commit()
            after_commit_at = time.perf_counter()
            pool_status = get_async_pool_status()
            logger.info(
                "[TIMING] workflow.patch_execution stage=after_commit execution_id=%s "
                "status=%s total_ms=%.2f commit_ms=%.2f pool_checked_out=%s "
                "pool_overflow=%s pool_usage_percent=%s",
                execution_id,
                getattr(execution, "status", None),
                (after_commit_at - started_at) * 1000,
                (after_commit_at - before_commit_at) * 1000,
                pool_status["checked_out"],
                pool_status["overflow"],
                pool_status["usage_percent"],
            )
            await self._delete_runtime_execution_snapshot_async(execution_id)
            return self._build_execution_ref(execution)

    async def _add_message_async(
            self,
            conversation_id: uuid.UUID,
            role: str,
            content: str,
            meta_data: Optional[dict] = None,
            message_id: Optional[uuid.UUID] = None,
            status: str = "completed",
            parent_message_id: Optional[uuid.UUID] = None,
    ) -> Message:
        async with get_async_db_context() as db:
            conversation = await db.get(Conversation, conversation_id)
            if not conversation:
                raise BusinessException(
                    f"会话不存在: conversation_id={conversation_id}",
                    code=BizCode.NOT_FOUND,
                )

            message = Message(
                id=message_id if message_id else uuid.uuid4(),
                conversation_id=conversation_id,
                role=role,
                content=content,
                meta_data=meta_data,
                status=status,
                parent_message_id=parent_message_id,
            )
            db.add(message)

            conversation.message_count = (conversation.message_count or 0) + 1
            if conversation.message_count <= 2 and role == "user":
                conversation.title = content[:50] + ("..." if len(content) > 50 else "")

            await db.commit()
            await db.refresh(message)
            return message

    async def _finalize_completed_execution_async(
            self,
            *,
            execution_id: str,
            conversation_id: uuid.UUID,
            human_message: str,
            human_meta: Optional[dict],
            user_message_id: Optional[uuid.UUID],
            from_message_id: Optional[uuid.UUID],
            assistant_message_id: Optional[uuid.UUID],
            assistant_message: str,
            assistant_meta: Optional[dict],
            workflow_output_data: Any,
            token_usage: Any,
    ) -> WorkflowExecutionRef:
        # Enqueue message persistence to BatchPersistQueue (async, non-blocking)
        if conversation_id:
            from .batch_persist_queue import BatchPersistQueue, PersistTask
            await BatchPersistQueue.enqueue(PersistTask(
                task_type="save_messages",
                args={
                    "conversation_id": str(conversation_id),
                    "user_message_id_override": user_message_id or uuid.uuid4(),
                    "message_id_override": assistant_message_id or uuid.uuid4(),
                    "user_message_content": human_message,
                    "user_meta_override": human_meta or {"files": []},
                    "content_override": assistant_message,
                    "meta_override": assistant_meta or {},
                    "user_parent_message_id": from_message_id,
                    "sync_memory": False,  # workflow has its own memory ingestion via memory node
                },
            ))

        return await self._patch_execution_async(
            execution_id,
            status="completed",
            output_data=workflow_output_data,
            token_usage=token_usage,
        )

    async def _update_message_async(
            self,
            message_id: uuid.UUID,
            content: str | None = None,
            meta_data: dict | None = None,
    ) -> Message | None:
        async with get_async_db_context() as db:
            message = await db.get(Message, message_id)
            if not message:
                logger.warning(f"update_message: message_id={message_id} not found")
                return None
            if content is not None:
                message.content = content
            if meta_data is not None:
                message.meta_data = meta_data
            await db.commit()
            await db.refresh(message)
            return message

    async def _touch_conversation_async(self, conversation_id: uuid.UUID | str | None) -> None:
        if not conversation_id:
            return
        try:
            cid = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
        except (TypeError, ValueError):
            return
        async with get_async_db_context() as db:
            conversation = await db.get(Conversation, cid)
            if conversation:
                conversation.updated_at = utcnow_naive()
                await db.commit()

    async def _save_failed_conversation_async(
            self,
            conversation_id: uuid.UUID | None,
            message_id: uuid.UUID | None,
            human_message: str,
            human_meta: dict,
            error: str,
    ) -> None:
        if not conversation_id:
            return
        from .batch_persist_queue import BatchPersistQueue, PersistTask
        await BatchPersistQueue.enqueue(PersistTask(
            task_type="save_failed_message",
            args={
                "conversation_id": str(conversation_id),
                "message_id": message_id or uuid.uuid4(),
                "user_message_id": uuid.uuid4(),
                "user_message_content": human_message,
                "files_meta": (human_meta or {}).get("files", []),
                "error_message": "",
                "error_detail": error,
                "sync_memory": False,
            },
        ))

    async def _archive_resolved_intervention_async(
            self,
            execution_id: str,
            node_id: str,
            action_id: str,
            form_data: dict | None,
            kind: str | None,
    ) -> WorkflowExecutionRef | None:
        execution = await self._get_execution_async(execution_id)
        if not execution or not node_id:
            return execution

        current_context = execution.context or {}
        intr_ctx = dict(current_context.get("human_intervention") or {})
        interventions = [dict(i) for i in (intr_ctx.get("interventions") or [])]
        intervention_backlog = [dict(i) for i in (intr_ctx.get("intervention_backlog") or [])]
        resolved_interventions = [dict(i) for i in (intr_ctx.get("resolved_interventions") or [])]

        matched = None
        for idx, intr in enumerate(interventions):
            if intr.get("node_id") == node_id:
                updated = {**intr, "resolved_action_id": action_id, "resolved_form_data": form_data or {}}
                interventions[idx] = updated
                matched = updated
                break

        for idx, intr in enumerate(intervention_backlog):
            if intr.get("node_id") == node_id:
                updated = {**intr, "resolved_action_id": action_id, "resolved_form_data": form_data or {}}
                intervention_backlog[idx] = updated
                if matched is None:
                    matched = updated
                break

        if matched is None:
            return execution

        if not any(i.get("node_id") == node_id for i in resolved_interventions):
            resolved_interventions.append({
                **matched,
                "resolved_action_id": action_id,
                "resolved_form_data": form_data or {},
                "resolved_at": to_iso_z(utcnow_naive()),
                "resolved_kind": kind or "interrupt",
            })

        new_context = {
            **current_context,
            "human_intervention": {
                **intr_ctx,
                "interventions": interventions,
                "intervention_backlog": intervention_backlog,
                "resolved_interventions": resolved_interventions,
            },
        }
        await self._touch_conversation_async(intr_ctx.get("conversation_id"))
        return await self._patch_execution_async(execution_id, context=new_context)

    async def _persist_execution_artifacts_async(
            self,
            execution_id: str,
            workflow_config: WorkflowConfig | None = None,
    ) -> None:
        if self.SKIP_EXECUTION_ARTIFACT_PERSIST_FOR_EXPERIMENT:
            logger.info(
                "[EXPERIMENT] skip execution artifact persist execution_id=%s",
                execution_id,
            )
            return
        execution = await self._get_execution_async(execution_id)
        if not execution or not execution.output_data:
            return
        resolved_workflow_config = workflow_config
        if resolved_workflow_config is None and execution.workflow_config_id:
            async with get_async_db_context() as db:
                resolved_workflow_config = await db.get(WorkflowConfig, execution.workflow_config_id)
        if resolved_workflow_config is None:
            return
        await self._persist_workflow_node_executions_async(
            execution=execution,
            workflow_config=resolved_workflow_config,
            result=execution.output_data,
        )
        await self._refresh_workflow_debug_state_from_execution_async(
            execution,
            workflow_config=resolved_workflow_config,
        )

    def _prepare_node_execution_items(
            self,
            execution: WorkflowExecution | WorkflowExecutionRef,
            workflow_config: WorkflowConfig,
            result: dict[str, Any],
    ) -> tuple[uuid.UUID, uuid.UUID, list[dict[str, Any]]]:
        node_outputs = result.get("node_outputs") or {}
        if not isinstance(node_outputs, dict) or not node_outputs:
            return execution.id, execution.app_id, []

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
        return execution.id, execution.app_id, items

    async def _persist_workflow_node_executions_async(
            self,
            execution: WorkflowExecution | WorkflowExecutionRef,
            workflow_config: WorkflowConfig,
            result: dict[str, Any],
    ) -> None:
        from .batch_persist_queue import BatchPersistQueue, PersistTask

        execution_id, _app_id, items = self._prepare_node_execution_items(execution, workflow_config, result)
        if not items:
            return
        await BatchPersistQueue.enqueue(PersistTask(
            task_type="save_node_executions",
            args={"execution_id": str(execution_id), "items": items},
        ))

    async def _write_workflow_debug_state_async(
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
        if not manager.app_id:
            return {
                "cache": {},
                "snapshot": persist_payload["snapshot"],
                "messages": persist_payload["messages"],
                "execution_id": persist_payload["execution_id"],
                "source": persist_payload["source"],
                "node_order": persist_payload["node_order"],
            }

        now = utcnow_naive()
        cache_key = manager.build_cache_key({"kind": self.DEBUG_STATE_SOURCE})
        normalized_meta = normalize_cache_value(persist_payload["meta_data"] or {})
        normalized_result = normalize_cache_value(persist_payload["result_data"])
        normalized_input = normalize_cache_value({"kind": self.DEBUG_STATE_SOURCE})

        async with get_async_db_context() as db:
            stmt = (
                select(WorkflowNodeCache)
                .where(
                    WorkflowNodeCache.app_id == manager.app_id,
                    WorkflowNodeCache.node_id == self.DEBUG_STATE_NODE_ID,
                    WorkflowNodeCache.cache_key == cache_key,
                    WorkflowNodeCache.status == "active",
                )
                .order_by(desc(WorkflowNodeCache.updated_at), desc(WorkflowNodeCache.created_at))
                .limit(1)
            )
            cache = (await db.execute(stmt)).scalars().first()
            if cache and cache.expires_at and cache.expires_at <= now:
                cache.status = "expired"
                cache.invalidated_at = now
                cache = None

            if cache is None:
                cache = WorkflowNodeCache(
                    app_id=manager.app_id,
                    workflow_config_id=manager.workflow_config_id,
                    node_id=self.DEBUG_STATE_NODE_ID,
                    node_type=self.DEBUG_STATE_NODE_TYPE,
                    node_name=self.DEBUG_STATE_NODE_NAME,
                    cache_key=cache_key,
                    source=self.DEBUG_STATE_SOURCE,
                    status="active",
                    input_data=normalized_input,
                    result_data=normalized_result,
                    hit_count=0,
                    last_hit_at=None,
                    expires_at=None,
                    invalidated_at=None,
                    meta_data=normalized_meta,
                )
                db.add(cache)
            else:
                cache.workflow_config_id = manager.workflow_config_id
                cache.node_type = self.DEBUG_STATE_NODE_TYPE
                cache.node_name = self.DEBUG_STATE_NODE_NAME
                cache.source = self.DEBUG_STATE_SOURCE
                cache.status = "active"
                cache.input_data = normalized_input
                cache.result_data = normalized_result
                cache.expires_at = None
                cache.invalidated_at = None
                cache.meta_data = normalized_meta

            await db.commit()
        return {
            "cache": {},
            "snapshot": persist_payload["snapshot"],
            "messages": persist_payload["messages"],
            "execution_id": persist_payload["execution_id"],
            "source": persist_payload["source"],
            "node_order": persist_payload["node_order"],
        }

    async def _refresh_workflow_debug_state_from_execution_async(
            self,
            execution: WorkflowExecution | WorkflowExecutionRef,
            workflow_config: WorkflowConfig | None = None,
    ) -> None:
        resolved_workflow_config = workflow_config
        output_data = self._serialize_execution_value(execution.output_data or {})
        async with get_async_db_context() as db:
            if resolved_workflow_config is None and execution.workflow_config_id:
                resolved_workflow_config = await db.get(WorkflowConfig, execution.workflow_config_id)
            if resolved_workflow_config is None:
                return
            stmt = (
                select(WorkflowNodeExecution)
                .where(WorkflowNodeExecution.execution_id == execution.id)
                .order_by(WorkflowNodeExecution.execution_order)
            )
            node_executions = list((await db.execute(stmt)).scalars())
            snapshot = self._build_public_execution_snapshot_record(
                execution=execution,
                node_executions=node_executions,
                output_data=output_data if isinstance(output_data, dict) else {},
                workflow_config=resolved_workflow_config,
            )
        await self._write_workflow_debug_state_async(
            app_id=execution.app_id,
            workflow_config=resolved_workflow_config,
            snapshot=snapshot or self._build_default_debug_state_snapshot(resolved_workflow_config),
            messages=self._extract_execution_messages(execution),
            execution_id=execution.execution_id,
            source=self._get_execution_source(execution),
        )

    def get_execution_statistics(self, app_id: uuid.UUID) -> dict[str, Any]:
        """Get execution statistics.

        Args:
            app_id: Application ID

        Returns:
            Statistics dict
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
        from ..workflow.utils.file_processor import fetch_remote_file_meta

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
                return await self._resolve_local_file_async(item.get("upload_file_id"), file_type, origin_file_type)

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

        from ..workflow.utils.file_processor import (
            build_file_object_dict_from_meta,
            fetch_remote_file_meta,
        )

        async def _resolve_file(file: FileInput) -> dict:
            url = await self._get_file_url_async(file)
            file_type = str(file.type)
            origin_file_type = file.file_type or file_type

            if file.transfer_method.value == "local_file" and file.upload_file_id:
                fo = await self._resolve_local_file_async(file.upload_file_id, file_type, origin_file_type)
                return fo or build_file_object_dict_from_meta(
                    file_type=file_type, transfer_method="local_file",
                    origin_file_type=origin_file_type,
                    file_id=str(file.upload_file_id), url=url,
                    file_name=None, file_size=None, file_ext=None, content_type=None,
                )
            return await fetch_remote_file_meta(url, file_type, origin_file_type)

        return await asyncio.gather(*(_resolve_file(file) for file in files))

    async def _get_file_url_async(self, file: FileInput) -> str:
        from ..config import current_settings
        from ..models.file_metadata_model import FileMetadata

        if file.transfer_method == TransferMethod.REMOTE_URL:
            return file.url

        file_id = file.upload_file_id
        async with get_async_db_context() as db:
            file_metadata = await db.scalar(
                select(FileMetadata).where(
                    FileMetadata.id == file_id,
                    FileMetadata.status == "completed",
                )
            )

        if not file_metadata:
            raise BusinessException(
                f"文件不存在或已删除: {file_id}",
                BizCode.NOT_FOUND
            )

        return f"{current_settings().file_local_server_url}/storage/permanent/{file_id}"

    async def _resolve_local_file_async(
            self,
            upload_file_id,
            file_type: str,
            origin_file_type: str,
    ) -> dict | None:
        """Async version of resolve_local_file_object_dict."""
        from ..workflow.utils.file_processor import build_file_object_dict_from_meta
        from ..models.file_metadata_model import FileMetadata
        from ..config import current_settings
        import uuid as _uuid

        try:
            fid = _uuid.UUID(str(upload_file_id))
        except ValueError:
            return None

        async with get_async_db_context() as db:
            meta = await db.scalar(
                select(FileMetadata).where(
                    FileMetadata.id == fid,
                    FileMetadata.status == "completed",
                )
            )
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

    async def _resolve_start_node_file_variables(
            self,
            start_node_vars: list[dict[str, Any]],
            variables: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve file-type runtime values among the start node variables.
        
        For variables of type file or array[file], when the user passes a
        FileInput-format dict/list, convert it into FileObject format.
        
        Args:
            start_node_vars: Start node variable definitions
            variables: User-supplied variable value dict
            
        Returns:
            The resolved variable value dict
        """
        from ..workflow.utils.file_processor import (
            build_file_object_dict_from_meta,
            fetch_remote_file_meta,
        )

        resolved = dict(variables)

        async def _resolve_runtime_file(value: dict[str, Any]) -> dict[str, Any] | None:
            url = value.get("url", "")
            transfer_method = value.get("transfer_method", "local_file" if value.get("upload_file_id") else "remote_url")
            file_type = str(FileType.trans(value.get("type", "document")))
            origin_file_type = value.get("file_type") or file_type

            if transfer_method == "local_file" and value.get("upload_file_id"):
                fo = await self._resolve_local_file_async(value["upload_file_id"], file_type, origin_file_type)
                return fo or build_file_object_dict_from_meta(
                    file_type=file_type,
                    transfer_method="local_file",
                    origin_file_type=origin_file_type,
                    file_id=str(value["upload_file_id"]),
                    url=url,
                )
            if url:
                return await fetch_remote_file_meta(url, file_type, origin_file_type)
            return None

        pending: list[tuple[str, asyncio.Task]] = []
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
                pending.append((var_name, asyncio.create_task(_resolve_runtime_file(value))))

            elif var_type == "array[file]" and isinstance(value, list):
                async def _resolve_array(items: list[Any]) -> list[Any]:
                    async_indexes = [
                        idx for idx, item in enumerate(items)
                        if isinstance(item, dict) and not item.get("is_file")
                    ]
                    resolved_async_items = await asyncio.gather(
                        *(_resolve_runtime_file(items[idx]) for idx in async_indexes)
                    )
                    resolved_items = list(items)
                    for idx, resolved_item in zip(async_indexes, resolved_async_items):
                        resolved_items[idx] = resolved_item
                    return [
                        item for item in resolved_items
                        if not (isinstance(item, dict) and not item.get("is_file")) and item is not None
                    ]

                pending.append((var_name, asyncio.create_task(_resolve_array(value))))

        for var_name, task in pending:
            resolved_value = await task
            if resolved_value is not None:
                resolved[var_name] = resolved_value

        return resolved

    @staticmethod
    def _iso_to_ms(iso_str: str | None) -> int | None:
        if not iso_str:
            return None
        try:
            return to_timestamp_ms(parse_iso_to_utc_naive(iso_str))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_iso_datetime(iso_str: str | None) -> datetime.datetime | None:
        try:
            return as_utc_aware(parse_iso_to_utc_naive(iso_str))
        except (ValueError, TypeError):
            return None

    @classmethod
    def _intervention_is_expired(cls, intervention: dict | None) -> bool:
        timeout_at = cls._parse_iso_datetime((intervention or {}).get("timeout_at"))
        if timeout_at is None:
            return False
        return timeout_at <= utcnow()

    @staticmethod
    def _find_intervention_by_node_id(interventions: list[dict], node_id: str) -> dict | None:
        return next(
            (
                intr for intr in interventions
                if isinstance(intr, dict) and intr.get("node_id") == node_id
            ),
            None,
        )

    @staticmethod
    def _resolved_intervention_node_ids(intervention_ctx: dict) -> set[str]:
        return {
            i.get("node_id")
            for i in (intervention_ctx.get("resolved_interventions") or [])
            if i.get("node_id")
        }

    def _touch_conversation(self, conversation_id: uuid.UUID | str | None) -> None:
        if not conversation_id:
            return
        try:
            cid = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
        except (TypeError, ValueError):
            return
        conversation = self.db.get(Conversation, cid)
        if conversation:
            conversation.updated_at = utcnow_naive()

    @staticmethod
    def _build_intervention_timeout_event(
            execution_id: str,
            node_id: str,
            intervention_ctx: dict | None = None,
    ) -> dict:
        intervention_ctx = intervention_ctx or {}
        interventions = [
            *(intervention_ctx.get("interventions") or []),
            *(intervention_ctx.get("intervention_backlog") or []),
        ]
        intr = next((i for i in interventions if i.get("node_id") == node_id), None)
        return {
            "event": "intervention_timeout",
            "data": {
                "execution_id": execution_id,
                "node_id": node_id,
                "node_name": (intr or {}).get("node_name", ""),
                "action_id": "__timeout__",
                "timeout_at": (intr or {}).get("timeout_at"),
            },
        }

    @staticmethod
    def _node_config_from_workflow(workflow_config: Any, node_id: str) -> dict | None:
        if not workflow_config or not node_id:
            return None
        if isinstance(workflow_config, dict):
            nodes = workflow_config.get("nodes") or []
        else:
            nodes = getattr(workflow_config, "nodes", None) or []
        return next((n for n in nodes if isinstance(n, dict) and n.get("id") == node_id), None)

    @classmethod
    def _calculate_visible_intervention_timeout_at(
            cls,
            workflow_config: Any,
            node_id: str,
    ) -> datetime.datetime | None:
        node_cfg = cls._node_config_from_workflow(workflow_config, node_id)
        if not node_cfg or node_cfg.get("type") != "human-intervention":
            return None

        config = node_cfg.get("config") or {}
        timeout_cfg = config.get("timeout")
        if timeout_cfg is None:
            timeout_cfg = {"unit": "minutes", "value": 1}

        if isinstance(timeout_cfg, dict):
            unit = timeout_cfg.get("unit", "minutes")
            value = timeout_cfg.get("value", 1)
        else:
            unit = getattr(timeout_cfg, "unit", "minutes")
            value = getattr(timeout_cfg, "value", 1)

        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 1
        if value <= 0:
            return None

        if unit == "days":
            delta = datetime.timedelta(days=value)
        elif unit == "hours":
            delta = datetime.timedelta(hours=value)
        elif unit == "seconds":
            delta = datetime.timedelta(seconds=value)
        else:
            delta = datetime.timedelta(minutes=value)
        return utcnow() + delta

    def _activate_visible_intervention_timeout(
            self,
            execution_id: str,
            node_id: str,
            workflow_config: Any,
    ) -> str | None:
        timeout_at = self._calculate_visible_intervention_timeout_at(workflow_config, node_id)
        if timeout_at is None:
            return None

        from ..workflow.nodes.human_intervention.node import InterventionRegistry, TimeoutEntry
        InterventionRegistry.register_timeout(TimeoutEntry(
            execution_id=execution_id,
            node_id=node_id,
            timeout_at=timeout_at,
        ))
        return to_iso_z(timeout_at)

    @staticmethod
    def _replace_intervention_timeout_at(
            interventions: list[dict],
            node_id: str,
            timeout_at: str | None,
    ) -> list[dict]:
        updated = []
        for intr in interventions:
            item = dict(intr)
            if item.get("node_id") == node_id:
                item["timeout_at"] = timeout_at
            updated.append(item)
        return updated

    def _activate_intervention_event_timeout(
            self,
            execution_id: str,
            event: dict,
            workflow_config: Any,
    ) -> tuple[dict, str | None]:
        node_id = (event.get("data") or {}).get("node_id", "")
        timeout_at = self._activate_visible_intervention_timeout(
            execution_id, node_id, workflow_config
        )
        if timeout_at is not None:
            event = {
                **event,
                "data": {
                    **(event.get("data") or {}),
                    "timeout_at": self._iso_to_ms(timeout_at),
                },
            }
        return event, timeout_at

    @staticmethod
    def _first_visible_intervention(interventions: list[dict]) -> list[dict]:
        return [dict(interventions[0])] if interventions else []

    @staticmethod
    def _interventions_by_node_ids(
            interventions: list[dict],
            node_ids: set[str],
    ) -> list[dict]:
        return [
            dict(intr)
            for intr in interventions
            if intr.get("node_id") in node_ids
        ]

    def _set_human_intervention_state(
            self,
            execution: WorkflowExecution | WorkflowExecutionRef,
            visible_interventions: list[dict] | None = None,
            backlog_interventions: list[dict] | None = None,
    ) -> None:
        current_context = execution.context or {}
        intr_ctx = dict(current_context.get("human_intervention") or {})
        if visible_interventions is not None:
            intr_ctx["interventions"] = [dict(i) for i in visible_interventions]
        if backlog_interventions is not None:
            intr_ctx["intervention_backlog"] = [dict(i) for i in backlog_interventions]
        execution.context = {
            **current_context,
            "human_intervention": intr_ctx,
        }

    def _archive_resolved_intervention(
            self,
            execution: WorkflowExecution,
            node_id: str,
            action_id: str,
            form_data: dict | None,
            kind: str | None,
    ) -> None:
        """Persist a submitted human-intervention node as resolved history."""
        if not node_id:
            return

        current_context = execution.context or {}
        intr_ctx = dict(current_context.get("human_intervention") or {})
        interventions = [dict(i) for i in (intr_ctx.get("interventions") or [])]
        intervention_backlog = [
            dict(i) for i in (intr_ctx.get("intervention_backlog") or [])
        ]
        resolved_interventions = [
            dict(i) for i in (intr_ctx.get("resolved_interventions") or [])
        ]

        matched = None
        for idx, intr in enumerate(interventions):
            if intr.get("node_id") == node_id:
                updated = {
                    **intr,
                    "resolved_action_id": action_id,
                    "resolved_form_data": form_data or {},
                }
                interventions[idx] = updated
                matched = updated
                break

        for idx, intr in enumerate(intervention_backlog):
            if intr.get("node_id") == node_id:
                updated = {
                    **intr,
                    "resolved_action_id": action_id,
                    "resolved_form_data": form_data or {},
                }
                intervention_backlog[idx] = updated
                if matched is None:
                    matched = updated
                break

        if matched is None:
            return

        already_archived = any(
            i.get("node_id") == node_id for i in resolved_interventions
        )
        if not already_archived:
            resolved_interventions.append({
                **matched,
                "resolved_action_id": action_id,
                "resolved_form_data": form_data or {},
                "resolved_at": to_iso_z(utcnow_naive()),
                "resolved_kind": kind or "interrupt",
            })

        execution.context = {
            **current_context,
            "human_intervention": {
                **intr_ctx,
                "interventions": interventions,
                "intervention_backlog": intervention_backlog,
                "resolved_interventions": resolved_interventions,
            },
        }
        self._touch_conversation(intr_ctx.get("conversation_id"))
        self.db.commit()

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
                        "message_id": payload.get("message_id"),
                        "execution_id": payload.get("execution_id"),
                        "user_message_id": payload.get("user_message_id"),
                    }
                }
            case "workflow_end":
                data = {
                    "status": payload.get("status", "completed"),
                    "elapsed_time": payload.get("elapsed_time"),
                    "message_length": len(payload.get("output", "")),
                    "error": payload.get("error", ""),
                    "output": payload.get("output", ""),
                    "execution_id": payload.get("execution_id"),
                    "total_tokens": (payload.get("token_usage") or {}).get("total_tokens", 0) or 0,
                }
                if payload.get("error_node_id"):
                    data["error_node_id"] = payload["error_node_id"]
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
        if internal_event.get("event") == "message":
            internal_event = {
                **internal_event,
                "data": dict(internal_event.get("data") or {}),
            }
            internal_event["data"].pop("provisional_node_id", None)
            internal_event["data"].pop("attempt", None)
        if public:
            mapped = self._map_public_event(internal_event)
        else:
            mapped = internal_event
        return mapped

    def _get_memory_store_info(self, workspace_id: uuid.UUID) -> tuple[str, str]:
        storage_type = get_workspace_storage_type_without_auth(self.db, workspace_id)
        user_rag_memory_id = ""
        # Fall back to the default 'neo4j' when storage_type is None
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

    async def _get_memory_store_info_async(self, workspace_id: uuid.UUID) -> tuple[str, str]:
        """Return (storage_type, user_rag_memory_id) with Redis caching.

        Workspace storage type and RAG memory knowledge id change infrequently,
        so this lookup is cached for 5 minutes to avoid a DB round-trip on
        every workflow request.
        """
        cache_key = f"wf_mem_store:{workspace_id}"
        cached = await get_json_async(cache_key)
        if cached is not CACHE_MISS and isinstance(cached, dict):
            return cached.get("storage_type", "neo4j"), cached.get("user_rag_memory_id", "")

        from ..models.workspace_model import Workspace
        from ..models.knowledge_model import Knowledge

        async with get_async_db_context() as db:
            result = await db.execute(
                select(Workspace.storage_type).where(Workspace.id == workspace_id)
            )
            storage_type = result.scalar_one_or_none()
            user_rag_memory_id = ""

            if not storage_type:
                storage_type = "neo4j"
                logger.warning(
                    f"Storage type not set for workspace {workspace_id}, using default: neo4j"
                )

            if storage_type == "rag":
                result = await db.execute(
                    select(Knowledge.id).where(
                        Knowledge.name == "USER_RAG_MERORY",
                        Knowledge.workspace_id == workspace_id,
                        Knowledge.status == 1,
                    ).limit(1)
                )
                knowledge_id = result.scalar_one_or_none()
                if knowledge_id:
                    user_rag_memory_id = str(knowledge_id)
                else:
                    logger.warning(
                        f"No knowledge base named 'USER_RAG_MEMORY' found, "
                        f"workspace_id: {workspace_id}, will use neo4j storage"
                    )
                    storage_type = "neo4j"

        await set_json_async(cache_key, {
            "storage_type": storage_type,
            "user_rag_memory_id": user_rag_memory_id,
        }, ttl=300)
        return storage_type, user_rag_memory_id

    @staticmethod
    def _extract_human_message_and_meta(
            final_messages: list[dict],
            fallback_message: str,
            fallback_files: list[dict] | None = None
    ) -> tuple[str, dict]:
        """Extract the user message and file metadata from the message list, falling back to the payload data."""
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
        """Write the user message and the assistant error message into the conversation on failure."""
        if not conversation_id:
            return
        self.conversation_service.add_message(
            conversation_id=conversation_id,
            role="user",
            content=human_message,
            meta_data=human_meta,
        )
        self.conversation_service.add_message(
            **({"message_id": message_id} if message_id else {}),
            conversation_id=conversation_id,
            role="assistant",
            content="",
            meta_data={"error": error},
        )

    @staticmethod
    def _supports_conversation(config: WorkflowConfig) -> bool:
        return getattr(config, "workflow_type", "workflow") == "workflow"

    # ==================== Regeneration ====================

    def _resolve_workflow_config(
            self,
            app: App,
            workspace_id: uuid.UUID,
    ) -> WorkflowConfig:
        """Resolve the workflow config: shared apps read the published snapshot, own apps read the draft. Reuses the is_shared logic of draft_run."""
        is_shared = app.workspace_id != workspace_id
        if is_shared:
            if not app.current_release_id:
                raise BusinessException("该应用尚未发布，无法使用", BizCode.AGENT_CONFIG_MISSING)
            release = self.db.get(AppRelease, app.current_release_id)
            if not release:
                raise BusinessException("发布版本不存在", BizCode.AGENT_CONFIG_MISSING)
            # Same as the shared branch of draft_run: rebuild the config via AppService
            # so the real config id is carried along
            from .app_service import AppService
            return AppService(self.db)._workflow_config_from_release(release)
        return self.check_config(app.id)

    def _assert_regenerate_quota(self, message_id: uuid.UUID) -> None:
        """Check whether this round has hit the version cap; raise BusinessException if exceeded.

        Counts undeleted assistant versions under the same parent_message_id
        (including the original reply). In historical data original_msg may lack
        parent_message_id (not counted by the query), so add 1 in that case.
        """
        from ..models import Message
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        parent_msg = self._locate_parent_user_message(original_msg)
        existing = self.db.query(Message).filter(
            Message.parent_message_id == parent_msg.id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
        ).count()
        if not original_msg.parent_message_id:
            existing += 1
        if existing >= self.MAX_REGENERATE_VERSIONS:
            raise BusinessException(
                f"该回复已达最大版本数（{self.MAX_REGENERATE_VERSIONS} 个），无法继续重新生成",
                BizCode.VERSION_LIMIT_EXCEEDED,
            )

    def _next_version(self, original_msg: "Message") -> int:
        """Compute the version number for a regenerated message: max version under the same parent_message_id + 1.

        In multi-branch scenarios the user may switch to an older version and
        regenerate; using original.version + 1 would collide with an already
        existing newer version and scramble version switching. Take the max
        version within the group + 1 instead.
        """
        from ..models import Message
        base = original_msg.version or 1
        if not original_msg.parent_message_id:
            return base + 1
        siblings = self.db.query(Message).filter(
            Message.parent_message_id == original_msg.parent_message_id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
        ).all()
        for s in siblings:
            if s.version and s.version > base:
                base = s.version
        return base + 1

    def _locate_parent_user_message(self, original_msg: "Message") -> "Message":
        """Locate the parent user message of the original assistant message.

        Lookup order:
        1. Prefer parent_message_id; fall back to time-based backtrace when it is
           empty or points to a non-user message;
        2. Backtrace to the latest user message in the same conversation whose
           created_at is earlier than or equal to the original message (not
           filtered by is_deleted, since that message is still the actual
           question of this round even after deletion; uses <= rather than < to
           cover the edge case where user/assistant rows share the same
           millisecond);
        3. If still not found, relax to the latest user message in the
           conversation (last-resort fallback covering dirty data with
           out-of-order created_at).

        If the located parent message was soft-deleted (the user accidentally
        deleted the original question before regenerating), restore it
        (is_deleted back to False) and continue instead of raising: an accidental
        deletion must not make the round permanently non-regenerable.
        """
        from ..models import Message
        parent_msg = None
        if original_msg.parent_message_id:
            candidate = self.db.get(Message, original_msg.parent_message_id)
            if candidate and candidate.role == "user":
                parent_msg = candidate
        if not parent_msg:
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at <= original_msg.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
        if not parent_msg:
            # Fallback: latest user message in the conversation (ignoring time order),
            # covering dirty data with abnormal created_at
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
        if not parent_msg:
            raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
        if original_msg.parent_message_id != parent_msg.id:
            original_msg.parent_message_id = parent_msg.id
            self.db.commit()
        if parent_msg.is_deleted:
            parent_msg.is_deleted = False
            self.db.commit()
            logger.info(
                "重新生成时自动恢复被删除的父 user 消息",
                extra={
                    "parent_message_id": str(parent_msg.id),
                    "assistant_message_id": str(original_msg.id),
                    "conversation_id": str(original_msg.conversation_id),
                },
            )
        return parent_msg

    def _resolve_parent_user_message(self, msg) -> Optional["MessageModel"]:
        """Read-only lookup of the parent user message for an assistant message (returns None, never raises).

        Same strategy as _locate_parent_user_message, but for read paths (branch
        chain / sibling rebuild), so a missing parent must not raise. Prefers
        parent_message_id (used directly when the parent is a user message); when
        it is empty or points to a non-user message, backtraces to the latest
        user message in the same conversation whose created_at is earlier than
        this message. This fallback covers historical assistant messages paused
        on the human-intervention path that lack parent_message_id.
        """
        from ..models import Message as MessageModel
        if msg.parent_message_id:
            parent = self.db.get(MessageModel, msg.parent_message_id)
            if parent and parent.role == "user":
                return parent
        if msg.created_at is None:
            return None
        return self.db.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == msg.conversation_id,
                MessageModel.role == "user",
                MessageModel.created_at < msg.created_at,
            )
            .order_by(MessageModel.created_at.desc())
            .limit(1)
        ).first()

    def _resolve_from_message_id(
        self,
        from_message_id: Optional[str],
        conversation_id: Optional[uuid.UUID],
    ) -> Optional[uuid.UUID]:
        """Resolve the parent_message_id for this round's user message (the previous assistant message id).

        Prefers the frontend-supplied from_message_id (multi-branch scenarios
        where the branch to continue from is explicit); when missing, falls back
        to the latest non-deleted assistant message id in the conversation
        (ordered by created_at desc, i.e. the most recently generated one) so the
        multi-turn chain never breaks — if the frontend omits from_message_id,
        the second-turn user message will not become an orphan (parent=null).
        Returns None on the first turn with no persisted assistant message.
        """
        if from_message_id:
            try:
                return uuid.UUID(from_message_id)
            except (ValueError, TypeError):
                return None
        if not conversation_id:
            return None
        from ..models import Message as MessageModel
        # The fallback prefers the assistant with is_current=True (the version
        # the user currently selected) over the most recently created one (a
        # regenerated version has a later created_at but the user may have
        # switched back to an older version). Otherwise, when the user sends a
        # new message from the version-1 view, parent would wrongly point to
        # version 2, making the message invisible on the version-1 branch but
        # present under version 2. Aligned with _get_last_current_assistant_id.
        last = self.db.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == "assistant",
                MessageModel.is_deleted.is_not(True),
                MessageModel.is_current.is_(True),
            )
            .order_by(MessageModel.created_at.desc())
            .limit(1)
        ).first()
        if last:
            return last.id
        # When no assistant carries is_current (historical data / abnormal state),
        # fall back to the latest one
        last = self.db.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == "assistant",
                MessageModel.is_deleted.is_not(True),
            )
            .order_by(MessageModel.created_at.desc())
            .limit(1)
        ).first()
        return last.id if last else None

    async def _resolve_from_message_id_async(
        self,
        from_message_id: Optional[str],
        conversation_id: Optional[uuid.UUID],
    ) -> Optional[uuid.UUID]:
        if from_message_id:
            try:
                return uuid.UUID(from_message_id)
            except (ValueError, TypeError):
                return None
        if not conversation_id:
            return None

        from ..models import Message as MessageModel

        async with get_async_db_context() as db:
            result = await db.execute(
                select(MessageModel.id)
                .where(
                    MessageModel.conversation_id == conversation_id,
                    MessageModel.role == "assistant",
                    MessageModel.is_deleted.is_not(True),
                )
                .order_by(desc(MessageModel.is_current), MessageModel.created_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    def _locate_execution_for_message(
            self, original_msg: "Message", conversation_id: uuid.UUID
    ) -> Optional[WorkflowExecution]:
        """Locate the workflow execution record that produced this assistant message.

        Prefers meta_data.execution_id on the message; when empty, backtraces to
        the latest completed execution in the same conversation whose created_at
        is not later than the message.
        """
        exec_id = (original_msg.meta_data or {}).get("execution_id") if original_msg.meta_data else None
        if exec_id:
            execution = self.execution_repo.get_by_execution_id(str(exec_id))
            if execution:
                return execution
        executions = self.execution_repo.get_by_conversation_id(
            conversation_id=conversation_id,
            status="completed",
            limit_count=20,
        ) or []
        # Take the last one whose created_at is not later than the original message
        candidates = [
            e for e in executions
            if e.created_at is not None and original_msg.created_at is not None
            and e.created_at <= original_msg.created_at
        ]
        if not candidates:
            candidates = list(executions)
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.created_at or datetime.datetime.min, reverse=True)
        return candidates[0]

    def _reconstruct_input(
            self,
            execution: Optional[WorkflowExecution],
            parent_msg: "Message",
    ) -> dict[str, Any]:
        """Restore the full input from the original execution.input_data (variables / files / trigger_*).

        message is not restored here — the caller uses parent_msg.content (this
        round's user question). When no execution is available, falls back to the
        parent message content and empty variables.
        """
        input_snapshot: dict[str, Any] = {
            "variables": {},
            "files": [],
            "trigger_type": "manual",
            "trigger_id": None,
            "trigger_meta": None,
            "trigger_payload": None,
        }
        if execution and execution.input_data and isinstance(execution.input_data, dict):
            data = execution.input_data
            input_snapshot["variables"] = data.get("variables", {}) or {}
            input_snapshot["trigger_type"] = data.get("trigger_type", "manual") or "manual"
            input_snapshot["trigger_id"] = data.get("trigger_id")
            input_snapshot["trigger_meta"] = data.get("trigger_meta")
            input_snapshot["trigger_payload"] = data.get("trigger_payload")
            # files are stored in input_data as a list of serialized dicts
            raw_files = data.get("files", []) or []
            input_snapshot["files"] = self._files_from_stored(raw_files, parent_msg)
        else:
            # Degrade to restoring from the parent message's meta_data.files
            input_snapshot["files"] = self._files_from_stored([], parent_msg)
        return input_snapshot

    @staticmethod
    def _files_from_stored(
            stored_files: list[dict[str, Any]],
            parent_msg: "Message",
    ) -> list[FileInput]:
        """Restore stored file dicts into a FileInput list, falling back to the parent message's meta_data.files."""
        files: list[FileInput] = []
        raw_list = list(stored_files or [])
        if not raw_list and parent_msg and parent_msg.meta_data:
            raw_list = parent_msg.meta_data.get("files", []) or []
        for f in raw_list:
            if isinstance(f, FileInput):
                files.append(f)
                continue
            if not isinstance(f, dict):
                continue
            try:
                files.append(FileInput(
                    type=FileType.trans(f.get("type", "document")) if hasattr(FileType, "trans") else FileType.DOCUMENT,
                    transfer_method=TransferMethod.REMOTE_URL if f.get("url") else TransferMethod.LOCAL_FILE,
                    url=f.get("url"),
                    file_type=f.get("file_type"),
                    name=f.get("name"),
                    size=f.get("size"),
                ))
            except Exception as e:
                logger.warning(f"重新生成还原文件信息失败: {e}")
                continue
        return files

    def _load_history_before_message(
            self,
            conversation_id: uuid.UUID,
            parent_msg: "Message",
    ) -> tuple[dict, list]:
        """Load the conversation history before this round's user message for regeneration, backtracing the parent chain.

        Starting from this round's user message, walk the parent_message_id chain
        to get the full pre-question context of the current branch; retry versions
        from the same round are naturally excluded (they share the parent but are
        not on this branch chain).
        """
        conv_messages = self._trace_context_chain(parent_msg)

        # conv_vars come from the latest completed execution before this round's
        # user message (accumulated conversation variables)
        conv_vars: dict[str, Any] = {}
        executions = self.execution_repo.get_by_conversation_id(
            conversation_id=conversation_id,
            status="completed",
            limit_count=20,
        ) or []
        prior_execs = [
            e for e in executions
            if e.created_at is not None and parent_msg.created_at is not None
            and e.created_at < parent_msg.created_at
        ]
        if prior_execs:
            prior_execs.sort(key=lambda e: e.created_at or datetime.datetime.min, reverse=True)
            last_state = prior_execs[0].output_data
            if isinstance(last_state, dict):
                variables = last_state.get("variables", {}) or {}
                conv_vars = variables.get("conv", {}) if isinstance(variables, dict) else {}
        return conv_vars, conv_messages



    async def regenerate(
            self,
            *,
            message_id: uuid.UUID,
            app_id: uuid.UUID,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            user_id: str,
            override_variables: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Workflow regeneration (non-streaming, multi-version support).

        Keeps the same conversation, the same user question, and the same prior
        conversation context; re-runs the workflow with the restored input to
        produce a new version assistant message (version+1, is_current=True),
        and marks the original version as not current.
        """
        from ..models import Message, Conversation

        self._assert_regenerate_quota(message_id)
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        conversation_id = original_msg.conversation_id
        parent_msg = self._locate_parent_user_message(original_msg)
        original_execution = self._locate_execution_for_message(original_msg, conversation_id)

        input_snapshot = self._reconstruct_input(original_execution, parent_msg)
        if override_variables is not None:
            # Merge rather than replace wholesale: overrides update only the given
            # variables, unspecified ones keep their snapshot values, so an empty
            # object does not wipe the original input
            merged = dict(input_snapshot.get("variables", {}) or {})
            merged.update(override_variables)
            input_snapshot["variables"] = merged

        history = self._load_history_before_message(conversation_id, parent_msg)

        # Mark the original version as not current
        original_msg.is_current = False
        self.db.commit()

        payload = DraftRunRequest(
            message=parent_msg.content or "",
            conversation_id=str(conversation_id),
            user_id=user_id,
            stream=False,
            variables=input_snapshot.get("variables", {}),
            files=input_snapshot.get("files", []),
            trigger_payload=input_snapshot.get("trigger_payload"),
        )

        # Suppress message persistence inside run; this method saves the
        # versioned message itself
        prev_suppress = getattr(self.conversation_service, "_suppress_message_save", False)
        self.conversation_service._suppress_message_save = True
        result = None
        try:
            result = await self.run(
                app_id=app_id,
                payload=payload,
                config=config,
                workspace_id=workspace_id,
                source=HitLogSource.CONSOLE,
                trigger_type=input_snapshot.get("trigger_type", "manual"),
                trigger_id=input_snapshot.get("trigger_id"),
                trigger_meta=input_snapshot.get("trigger_meta"),
                trigger_payload=input_snapshot.get("trigger_payload"),
                regenerate_mode=True,
                regenerate_history=history,
            )
        except Exception:
            # run raised: roll back is_current to avoid a "no current version"
            # state that would make downstream state inconsistent
            original_msg.is_current = True
            self.db.commit()
            raise
        finally:
            self.conversation_service._suppress_message_save = prev_suppress

        # Failure rollback: run returned but status is not completed
        if not result or result.get("status") != "completed":
            original_msg.is_current = True
            self.db.commit()
            raise BusinessException(
                code=BizCode.INTERNAL_ERROR,
                message=f"工作流重新生成失败: {result.get('error') if result else '无结果'}"
            )

        new_version = self._next_version(original_msg)
        new_message_id = uuid.uuid4()
        new_msg = Message(
            id=new_message_id,
            conversation_id=conversation_id,
            role="assistant",
            content=result.get("message") or result.get("output", "") or "",
            version=new_version,
            is_current=True,
            parent_message_id=parent_msg.id,
            meta_data={
                "usage": result.get("token_usage", {}),
                "audio_url": None,
                "citations": result.get("citations", []),
                "execution_id": result.get("execution_id"),
                "regenerated_from": str(message_id),
                "input_snapshot": {
                    "variables": input_snapshot.get("variables", {}),
                    "trigger_type": input_snapshot.get("trigger_type", "manual"),
                },
            },
            status="completed",
        )
        self.db.add(new_msg)
        conv = self.db.get(Conversation, conversation_id)
        if conv:
            conv.message_count += 1
        self.db.commit()
        self.db.refresh(new_msg)

        logger.info(
            "工作流重新生成成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_message_id),
                "version": new_version,
                "execution_id": result.get("execution_id"),
                "conversation_id": str(conversation_id),
            }
        )

        return {
            "message_id": str(new_msg.id),
            "message": new_msg.content,
            "version": new_version,
            "conversation_id": str(conversation_id),
            "execution_id": result.get("execution_id"),
            "citations": result.get("citations", []),
            "token_usage": result.get("token_usage", {}),
            "elapsed_time": result.get("elapsed_time"),
        }

    async def regenerate_stream(
            self,
            *,
            message_id: uuid.UUID,
            app_id: uuid.UUID,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            user_id: str,
            override_variables: Optional[dict[str, Any]] = None,
            pre_create_execution: bool = False,
    ):
        """Workflow regeneration (streaming, multi-version support).

        Yields SSE events: wraps run_stream events with a start event (carrying
        the new version number) and final persistence.

        Args:
            pre_create_execution: when True, pre-creates the `WorkflowExecution`
                row before yielding the `start` event and passes the execution
                object into `run_stream` for reuse, avoiding a duplicate creation
                inside run_stream. After pre-creation the start event carries the
                real `execution_id`, so the share-experience scenario can return
                the ID to the frontend at the head of the stream.
        """
        from ..models import Message, Conversation

        self._assert_regenerate_quota(message_id)
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        conversation_id = original_msg.conversation_id
        parent_msg = self._locate_parent_user_message(original_msg)
        # run_stream commits multiple times during execution (expire_on_commit=True
        # marks object attributes as expired); to avoid extra DB queries or
        # DetachedInstanceError on later access, snapshot the primary keys and
        # content needed for persistence into local variables instead of relying
        # on ORM lazy loading.
        parent_msg_id = parent_msg.id
        parent_msg_content = parent_msg.content or ""
        original_execution = self._locate_execution_for_message(original_msg, conversation_id)

        input_snapshot = self._reconstruct_input(original_execution, parent_msg)
        if override_variables is not None:
            # Merge rather than replace wholesale: overrides update only the given
            # variables, unspecified ones keep their snapshot values, so an empty
            # object does not wipe the original input
            merged = dict(input_snapshot.get("variables", {}) or {})
            merged.update(override_variables)
            input_snapshot["variables"] = merged

        history = self._load_history_before_message(conversation_id, parent_msg)

        original_msg.is_current = False
        self.db.commit()

        new_version = self._next_version(original_msg)
        new_message_id = uuid.uuid4()

        payload = DraftRunRequest(
            message=parent_msg_content,
            conversation_id=str(conversation_id),
            user_id=user_id,
            stream=True,
            variables=input_snapshot.get("variables", {}),
            files=input_snapshot.get("files", []),
            trigger_payload=input_snapshot.get("trigger_payload"),
        )

        # Share-experience scenario: pre-create the execution before yielding
        # start so the start event carries the real execution_id; also pass the
        # execution object through to run_stream for reuse, avoiding a second
        # create_execution inside it.
        preserved_execution = None
        if pre_create_execution:
            # The runtime WorkflowConfig object has no release_id/workflow_config_id
            # attributes (only `id`, which maps to workflow_config_id); release_id
            # reuses the original execution's record, matching the source used when
            # run_stream creates an execution internally on the non-pre-create path.
            preserved_execution = self.create_execution(
                workflow_config_id=config.id,
                app_id=app_id,
                release_id=original_execution.release_id if original_execution else None,
                trigger_type=input_snapshot.get("trigger_type", "manual"),
                # The triggered_by foreign key points at the internal users table;
                # user_id here may be an end-user ID, not a users.id, so passing it
                # would violate the foreign key — matching the other
                # create_execution calls inside run_stream, which all pass None.
                triggered_by=None,
                conversation_id=conversation_id,
                input_data=input_snapshot,
            )

        full_content = ""
        output_contents: dict[str, str] = {}
        output_order: list[str] = []

        def ensure_output_order(node_id: str) -> None:
            if node_id not in output_order:
                output_order.append(node_id)

        def rebuild_output_content() -> str:
            return "\n".join(
                output_contents[node_id]
                for node_id in output_order
                if output_contents.get(node_id)
            )

        def ordered_outputs() -> list[dict[str, Any]]:
            return [
                {
                    "node_id": node_id,
                    "content": output_contents[node_id],
                    "status": "completed",
                }
                for node_id in output_order
                if output_contents.get(node_id)
            ]

        token_usage: dict[str, Any] = {}
        citations: list[Any] = []
        elapsed_time: Optional[float] = None
        execution_id: Optional[str] = (
            preserved_execution.execution_id if preserved_execution is not None else None
        )
        stream_status = "completed"
        stream_error: Optional[str] = None
        paused_for_intervention = False

        yield {
            "event": "start",
            "data": {
                "conversation_id": str(conversation_id),
                "message_id": str(new_message_id),
                "execution_id": execution_id,
                "user_message_id": str(parent_msg_id),
                "version": new_version,
            },
        }

        prev_suppress = getattr(self.conversation_service, "_suppress_message_save", False)
        self.conversation_service._suppress_message_save = True
        stream_failed = False
        failure_error: Optional[str] = None
        try:
            async for event in self.run_stream(
                    app_id=app_id,
                    payload=payload,
                    config=config,
                    workspace_id=workspace_id,
                    source=HitLogSource.CONSOLE,
                    trigger_type=input_snapshot.get("trigger_type", "manual"),
                    trigger_id=input_snapshot.get("trigger_id"),
                    trigger_meta=input_snapshot.get("trigger_meta"),
                    trigger_payload=input_snapshot.get("trigger_payload"),
                    regenerate_mode=True,
                    regenerate_history=history,
                    regenerate_message_id=new_message_id,
                    preserved_execution=preserved_execution,
            ):
                event_type = event.get("event", "message")
                event_data = event.get("data", {}) or {}
                if event_type == "message":
                    content = event_data.get("content", "") or ""
                    output_node_id = event_data.get("node_id")
                    if output_node_id:
                        ensure_output_order(output_node_id)
                        output_contents[output_node_id] = (
                            output_contents.get(output_node_id, "") + content
                        )
                        full_content = rebuild_output_content()
                    else:
                        full_content += content
                elif event_type == "message_replace":
                    replacement = event_data.get("content", "") or ""
                    output_node_id = event_data.get("node_id")
                    if output_node_id:
                        ensure_output_order(output_node_id)
                        if replacement:
                            output_contents[output_node_id] = replacement
                        else:
                            output_contents.pop(output_node_id, None)
                        full_content = rebuild_output_content()
                    else:
                        full_content = replacement
                        output_contents.clear()
                        output_order.clear()
                elif event_type == "workflow_start":
                    # Regenerate: rewrite workflow_start's message_id to the new
                    # message id persisted by this method, consistent with
                    # regenerate_end. The placeholder id generated inside
                    # run_stream corresponds to no persisted message (message
                    # persistence is suppressed by _suppress_message_save in
                    # regenerate mode).
                    if isinstance(event.get("data"), dict):
                        event["data"]["message_id"] = str(new_message_id)
                elif event_type in ("workflow_end", "end"):
                    canonical_outputs = event_data.get("outputs")
                    if isinstance(canonical_outputs, list):
                        output_contents.clear()
                        output_order.clear()
                        for output in canonical_outputs:
                            if not isinstance(output, dict) or not output.get("node_id"):
                                continue
                            output_node_id = str(output["node_id"])
                            ensure_output_order(output_node_id)
                            output_contents[output_node_id] = output.get("content", "") or ""
                        full_content = rebuild_output_content()
                    canonical_output = event_data.get("output")
                    if isinstance(canonical_output, str):
                        full_content = canonical_output
                    token_usage = event_data.get("usage") or event_data.get("token_usage") or {}
                    citations = event_data.get("citations", []) or []
                    elapsed_time = event_data.get("elapsed_time")
                    execution_id = event_data.get("execution_id") or execution_id
                    if event_data.get("error"):
                        stream_error = event_data.get("error")
                        stream_status = "failed"
                        # Failure termination events from run_stream are re-emitted
                        # by this method after rollback, so the frontend does not
                        # first receive the inner workflow_end and then a duplicate
                        # failure termination event.
                        continue
                elif event_type == "intervention_required":
                    # Regeneration hit a manual-intervention pause: record the
                    # execution_id and pause flag so the new version message is
                    # persisted in the waiting-for-human form (same as the first pause).
                    execution_id = event_data.get("execution_id") or execution_id
                    paused_for_intervention = True
                yield event
        except Exception as e:
            # run_stream raised: mark failure and roll back below uniformly,
            # avoiding an in-stream exception that triggers "response already started"
            stream_failed = True
            failure_error = str(e)
        finally:
            self.conversation_service._suppress_message_save = prev_suppress

        # Failure rollback: run_stream raised or status is not completed
        if stream_failed or stream_status != "completed":
            # Rollback does not depend on ORM instance state; restore is_current
            # with a bulk UPDATE by primary key so a failed rollback cannot leave
            # the original version at is_current=False with no current reply this round.
            self.db.query(Message).filter(Message.id == message_id).update({"is_current": True})
            self.db.commit()
            yield {
                "event": "workflow_end",
                "data": {
                    "status": "failed",
                    "execution_id": execution_id,
                    "error": failure_error or stream_error or "工作流重新生成失败",
                },
            }
            return

        new_msg = Message(
            id=new_message_id,
            conversation_id=conversation_id,
            role="assistant",
            content=full_content,
            version=new_version,
            is_current=True,
            parent_message_id=parent_msg_id,
            meta_data={
                "usage": token_usage,
                "audio_url": None,
                "citations": citations,
                "execution_id": execution_id,
                "regenerated_from": str(message_id),
                **({"outputs": ordered_outputs()} if output_contents else {}),
                **({"waiting_human": True} if paused_for_intervention else {}),
                "input_snapshot": {
                    "variables": input_snapshot.get("variables", {}),
                    "trigger_type": input_snapshot.get("trigger_type", "manual"),
                },
            },
            status="completed",
        )
        self.db.add(new_msg)
        conv = self.db.get(Conversation, conversation_id)
        if conv:
            conv.message_count += 1
        self.db.commit()

        # run_stream's closing event type is workflow_end (not end), which the
        # share-experience controller filters out since it is not in the
        # SHARE_FORWARD_EVENTS whitelist; the stream would then jump from content
        # events straight to regenerate_end with an end missing in between. Emit a
        # standard end event explicitly here, semantically consistent with the end
        # event of a non-regenerate first round.
        yield {
            "event": "end",
            "data": {
                "status": stream_status,
                "elapsed_time": elapsed_time,
                "message_length": len(full_content),
                "error": stream_error,
                "output": full_content,
            },
        }

        yield {
            "event": "regenerate_end",
            "data": {
                "message_id": str(new_message_id),
                "version": new_version,
                "conversation_id": str(conversation_id),
                "execution_id": execution_id,
            },
        }

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

        from ..models import Conversation as ConversationModel

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

    async def _ensure_conversation_async(
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

        conversation_id = uuid.uuid4()
        async with get_async_db_context() as db:
            db.add(
                Conversation(
                    id=conversation_id,
                    app_id=app_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    is_draft=True,
                    title="草稿会话",
                )
            )
            await db.commit()
        return conversation_id

    def _trace_context_chain(self, end_msg) -> list[dict[str, Any]]:
        """Walk the parent_message_id chain upwards and return the conversation
        messages from the root up to but excluding end_msg.

        Tree branch model: user.parent = the preceding assistant (which reply it
        continues from), assistant.parent = this round's user. Walking the chain
        yields the full context of the current branch, unaffected by version
        switches within the same round.
        """
        from ..models import Message as MessageModel
        chain: list[MessageModel] = []
        visited: set = set()
        cur = end_msg
        while cur is not None and cur.parent_message_id:
            if cur.id in visited:
                break  # Cycle guard
            visited.add(cur.id)
            parent = self.db.get(MessageModel, cur.parent_message_id)
            if parent is None or parent.is_deleted:
                break
            chain.append(parent)
            cur = parent
        chain.reverse()  # Root first
        return [
            {"role": m.role, "content": m.content}
            for m in chain if m.role in ("user", "assistant")
        ]

    def _load_branch_chain(self, message_id) -> list:
        """Load the full linear chain of the branch message_id belongs to
        (ancestors upwards + message_id + descendants downwards).

        Walks downwards recursively along the is_current child (or the most
        recent child if none), yielding the branch's full follow-up conversation.
        Returns a list of Messages (root first).
        """
        from ..models import Message as MessageModel
        try:
            mid = uuid.UUID(message_id) if isinstance(message_id, str) else message_id
            msg = self.db.get(MessageModel, mid)
        except (ValueError, TypeError):
            return []
        if not msg:
            return []

        # Upwards: ancestor chain (root to msg, excluding msg)
        # When parent_message_id is missing and the current message is an
        # assistant, fall back to tracing back to the most recent user message
        # (historical assistant messages from the manual-intervention pause path
        # have no parent; otherwise this round's user would be lost).
        ancestors: list[MessageModel] = []
        visited_up: set = {msg.id}
        cur = msg
        while cur:
            nxt: MessageModel | None = None
            if cur.parent_message_id and cur.parent_message_id not in visited_up:
                parent = self.db.get(MessageModel, cur.parent_message_id)
                if parent:
                    nxt = parent
            elif not cur.parent_message_id and cur.role == "assistant":
                fb = self._resolve_parent_user_message(cur)
                if fb and fb.id not in visited_up:
                    nxt = fb
            if nxt is None:
                break
            visited_up.add(nxt.id)
            ancestors.append(nxt)
            cur = nxt
        ancestors.reverse()

        # Downwards: descendant chain (along parent_message_id children; when an
        # agent historical user message has no parent, fall back to finding the
        # next parentless user by time from the assistant, without pulling in
        # other branches' follow-ups)
        descendants: list[MessageModel] = []
        visited_down: set = {msg.id}
        cur = msg
        while True:
            # Prefer the is_current child via parent_message_id
            child = self.db.query(MessageModel).filter(
                MessageModel.parent_message_id == cur.id,
                MessageModel.is_current.is_(True),
            ).order_by(MessageModel.created_at.desc()).first()
            if child is None:
                # No is_current child; take the most recent child whose parent points at cur
                child = self.db.query(MessageModel).filter(
                    MessageModel.parent_message_id == cur.id,
                ).order_by(MessageModel.created_at.desc()).first()
            if child is None and cur.created_at is not None and cur.role == "assistant":
                # Fallback: agent historical user messages have no parent; from the
                # assistant find the next user by time. Only accept it as a child
                # when its parent is empty (legacy compatibility); if its parent
                # points at another assistant it belongs to a different branch and
                # must not be pulled in.
                child = self.db.query(MessageModel).filter(
                    MessageModel.conversation_id == cur.conversation_id,
                    MessageModel.is_deleted.is_not(True),
                    MessageModel.created_at > cur.created_at,
                    MessageModel.role == "user",
                    MessageModel.parent_message_id.is_(None),
                ).order_by(MessageModel.created_at.asc()).first()
            if child is None or child.id in visited_down:
                break
            visited_down.add(child.id)
            descendants.append(child)
            cur = child

        return ancestors + [msg] + descendants

    def _resolve_sibling_versions(self, m) -> list:
        """Return all versions of assistant message m from the same round
        (including m itself), sorted by version ascending.

        Same round = the same parent user message. Prefer exact aggregation by
        parent_message_id; when the parent is missing (historical first round
        from the manual-intervention pause path), resolve this round's user and
        aggregate by the time window [parent_user, next_user), so a legacy
        parentless first round and its regenerated sibling versions (parent
        pointing at the user) land in the same group. m itself is always in the
        result set (added as a fallback).
        """
        from ..models import Message as MessageModel
        parent_user = self._resolve_parent_user_message(m)
        if parent_user is None:
            return [m]
        next_user = None
        if parent_user.created_at is not None:
            next_user = self.db.scalars(
                select(MessageModel)
                .where(
                    MessageModel.conversation_id == m.conversation_id,
                    MessageModel.role == "user",
                    MessageModel.created_at > parent_user.created_at,
                    MessageModel.is_deleted.is_not(True),
                )
                .order_by(MessageModel.created_at.asc())
                .limit(1)
            ).first()
        # Primary set: parent points exactly at this round's user (no time window —
        # the parent already bounds it to this round, avoiding a regenerated
        # version whose created_at is later than next_user being wrongly excluded
        # by the window)
        siblings = self.db.query(MessageModel).filter(
            MessageModel.conversation_id == m.conversation_id,
            MessageModel.role == "assistant",
            MessageModel.is_deleted.is_not(True),
            MessageModel.parent_message_id == parent_user.id,
        ).order_by(MessageModel.version.asc()).all()
        existing_ids = {str(s.id) for s in siblings}
        # Supplementary set: legacy orphan first rounds with an empty parent
        # cannot be bounded by parent, so use the time window instead
        orphan_q = self.db.query(MessageModel).filter(
            MessageModel.conversation_id == m.conversation_id,
            MessageModel.role == "assistant",
            MessageModel.is_deleted.is_not(True),
            MessageModel.parent_message_id.is_(None),
        )
        if parent_user.created_at is not None:
            orphan_q = orphan_q.filter(MessageModel.created_at >= parent_user.created_at)
        if next_user is not None:
            orphan_q = orphan_q.filter(MessageModel.created_at < next_user.created_at)
        # Exclude the assistant message right before this turn's user message (opening
        # remark or the previous reply): its parent is empty and its created_at may
        # equal the user's (same-batch persistence, clock resolution), so the time
        # window above would wrongly claim it as a "version" of this turn.
        preceding_id = parent_user.parent_message_id
        for o in orphan_q.order_by(MessageModel.version.asc()).all():
            if preceding_id is not None and o.id == preceding_id:
                continue
            if str(o.id) not in existing_ids:
                siblings.append(o)
                existing_ids.add(str(o.id))
        # Fallback: m itself is always in the result set
        if str(m.id) not in existing_ids:
            siblings.append(m)
        siblings.sort(key=lambda s: (s.version or 0))
        return siblings

    def _build_branch_view(self, message_id, user_id: str | None = None) -> dict:
        """Build the branch view: the full chain of the branch message_id belongs
        to, plus per-version node execution details for each assistant version.

        Returns {
          messages: flat branch chain (ChatItem | ChatItem[])[] shaped to match
                    the frontend chatList directly — one item per round: a
                    single-message round is an object, a multi-version assistant
                    round is a sub-array (all siblings sharing the parent).
          node_executions_map: { <assistant_message_id>: [node detail dict, ...] }
                    Node execution details grouped by message id, reusing the log
                    system's _build_nodes_from_output_data serialization; used by
                    the frontend to rebuild subContent on a cache miss (fixes node
                    details being lost across refreshes).
          pending_intervention: { <message_id>: {execution_id, status, interventions} }
                    Manual-intervention info with the same structure as the log
                    system's /public/share endpoint (reusing
                    AppLogService.build_pending_intervention_map), used by the
                    frontend to backfill HITL node status after a version switch.
        }
        """
        from ..models import Message as MessageModel, WorkflowExecution, MessageFeedback
        from .app_log_service import AppLogService, _build_nodes_from_output_data
        chain = self._load_branch_chain(message_id)
        if not chain:
            return {"messages": [], "node_executions_map": {}, "pending_intervention": {}}

        def serialize(m: MessageModel) -> dict:
            fb_type, fb_content = feedback_map.get(m.id, (None, None))
            meta_out = m.meta_data
            # audio_status is persisted as a snapshot taken at the end event (usually
            # "pending"; TTS has not finished yet) and never written back. Override it
            # with the live FileMetadata status, same convention as the conversation
            # detail endpoint, so a switched version does not stay pending forever.
            if isinstance(meta_out, dict) and m.id in audio_status_map:
                meta_out = {**meta_out, "audio_status": audio_status_map[m.id]}
            return {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "version": m.version,
                "is_current": m.is_current,
                "created_at": to_timestamp_ms(m.created_at),
                "meta_data": meta_out,
                "status": m.status,
                "parent_message_id": str(m.parent_message_id) if m.parent_message_id else None,
                "is_favorited": m.id in favorited_ids,
                "feedback_type": fb_type,
                "feedback_content": fb_content,
            }

        # Preload all executions of this conversation for nearest-completion-time
        # fallback matching when meta_data.execution_id is missing (the original
        # first run's assistant message has no execution_id in meta_data, unlike
        # regenerated ones)
        conv_id = chain[0].conversation_id
        fallback_executions = self.db.query(WorkflowExecution).filter(
            WorkflowExecution.conversation_id == conv_id,
            WorkflowExecution.status.in_(["completed", "failed", "waiting_human", "timeout", "cancelled"]),
        ).all() if conv_id else []

        def _find_execution_by_time(target_msg: MessageModel):
            """Match an execution by nearest completion time (same fallback
            strategy as the log system; within 300s counts as the same one)."""
            target = target_msg.created_at
            best = None
            best_diff = None
            for ex in fallback_executions:
                ref = ex.completed_at or ex.started_at
                if not ref:
                    continue
                diff = abs((ref - target).total_seconds())
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = ex
            return best if (best is not None and best_diff is not None and best_diff <= 300) else None

        def build_nodes(m: MessageModel) -> list[dict]:
            """Get node execution details for this version (reusing log-system serialization).
            Prefer exact lookup by meta_data.execution_id; fall back to nearest
            completion time when missing (the original first round has no execution_id).
            When no WorkflowExecution matches (agent apps persist AgentExecution),
            fall back to AppLogService.get_message_node_executions, so agent
            trial-run version switches can also backfill subContent."""
            meta = m.meta_data if isinstance(m.meta_data, dict) else None
            # Assistant messages not produced by an execution (greetings etc.)
            # have no real execution record: their meta_data carries no execution
            # trace (execution_id / regenerated_from / usage); skip fallback
            # matching so they are not wrongly attached to a neighboring
            # execution by nearest time
            if meta and not any(k in meta for k in ("execution_id", "regenerated_from", "usage")):
                return []
            exec_id = meta.get("execution_id") if meta else None
            execution = self.execution_repo.get_by_execution_id(exec_id) if exec_id else _find_execution_by_time(m)
            if execution:
                # Prefer reading from the WorkflowNodeExecution table (the final
                # state persisted by _persist_workflow_node_executions; most
                # accurate). Fixes legacy cases where HITL nodes in output_data
                # still show waiting status (because the detached execution
                # object was never persisted).
                from ..models import WorkflowNodeExecution as _WNE
                from ..schemas.app_log_schema import AppLogNodeExecution as _NE
                tbl_nodes = self.node_execution_repo.get_by_execution_id(execution.id) if execution.id else []
                if tbl_nodes:
                    _ordered = sorted(tbl_nodes, key=lambda n: n.execution_order or 0)
                    _result = []
                    for n in _ordered:
                        _result.append(_NE(
                            node_id=n.node_id,
                            node_type=n.node_type or "unknown",
                            node_name=n.node_name,
                            status=n.status or "completed",
                            error=n.error_message,
                            input=n.input_data,
                            process=(n.meta_data or {}).get("process_data") if isinstance(n.meta_data, dict) else None,
                            output=n.output_data,
                            elapsed_time=n.elapsed_time,
                            token_usage=n.token_usage,
                            meta=n.meta_data,
                        ))
                    if _result:
                        return [n.model_dump() for n in _result]
                # No table rows; fall back to output_data (the case where a
                # waiting_human run has not been resumed to completion)
                if execution.output_data:
                    nodes = _build_nodes_from_output_data(execution.output_data)
                    if nodes:
                        return [n.model_dump() for n in nodes]
            # Agent fallback: agent apps persist AgentExecution.steps and have no
            # WorkflowExecution (versions whose save was skipped by regenerate
            # have no agent_execution and return [] — known limitation)
            return AppLogService(self.db).get_message_node_executions(m.id)

        # Parse the round structure first (collect all sibling versions for
        # assistant), gather all message ids, then batch-query the current
        # user's favorite status
        turns: list[tuple[str, Any]] = []
        all_ids: set = set()
        for m in chain:
            if m.is_deleted:
                continue
            if m.role == "user":
                turns.append(("user", m))
                all_ids.add(m.id)
            elif m.role == "assistant":
                siblings = self._resolve_sibling_versions(m)
                turns.append(("assistant", siblings))
                for s in siblings:
                    all_ids.add(s.id)

        favorited_ids: set = set()
        if user_id and all_ids:
            favorited_ids = {
                row[0]
                for row in self.db.query(MessageFeedback.message_id)
                .filter(
                    MessageFeedback.message_id.in_(all_ids),
                    MessageFeedback.user_id == user_id,
                    MessageFeedback.is_favorite.is_(True),
                )
                .all()
            }

        # Pre-query feedback for the current user to avoid feedbacks[0] cross-display
        # (picking an arbitrary row under multiple users misplaces the state)
        feedback_map: dict = {}
        if user_id and all_ids:
            feedback_rows = (
                self.db.query(
                    MessageFeedback.message_id,
                    MessageFeedback.feedback_type,
                    MessageFeedback.feedback_content,
                )
                .filter(
                    MessageFeedback.message_id.in_(all_ids),
                    MessageFeedback.user_id == user_id,
                )
                .all()
            )
            feedback_map = {row[0]: (row[1], row[2]) for row in feedback_rows}

        # TTS audio status: meta_data.audio_status stored at persist time is the snapshot
        # from the end event (usually "pending"); batch-query FileMetadata by the file_id
        # at the end of audio_url and override with the live status (same convention as
        # the share-side conversation detail endpoint).
        audio_status_map: dict = {}
        try:
            from ..models.file_metadata_model import FileMetadata
            _audio_fid_by_msg: dict = {}
            for _role, _payload in turns:
                for _m in ([_payload] if _role == "user" else _payload):
                    _meta = _m.meta_data if isinstance(_m.meta_data, dict) else None
                    _url = _meta.get("audio_url") if _meta else None
                    if _m.role != "assistant" or not _url:
                        continue
                    try:
                        _audio_fid_by_msg[_m.id] = uuid.UUID(str(_url).rstrip("/").split("/")[-1])
                    except (ValueError, IndexError):
                        audio_status_map[_m.id] = "unknown"
            if _audio_fid_by_msg:
                _status_by_fid = {
                    fm.id: fm.status
                    for fm in self.db.query(FileMetadata)
                    .filter(FileMetadata.id.in_(set(_audio_fid_by_msg.values())))
                    .all()
                }
                for _mid, _fid in _audio_fid_by_msg.items():
                    audio_status_map[_mid] = _status_by_fid.get(_fid, "unknown")
        except Exception as e:
            # Status backfill failure must not break the branch view itself; fall back
            # to the persisted value.
            logger.warning(f"分支视图回填 audio_status 失败（已降级）: {e}")

        items: list[Any] = []
        node_executions_map: dict[str, list[dict]] = {}
        for role, payload in turns:
            if role == "user":
                items.append(serialize(payload))
            else:
                # All assistant versions of the same round (for the version
                # switcher): aggregate by parent user, covering legacy parentless
                # first rounds from the manual-intervention pause path (see
                # _resolve_sibling_versions)
                siblings = payload
                serialized = [serialize(s) for s in siblings]
                # Flatten single versions to an object, keep multiple versions as
                # an array — matching the frontend chatList convention
                items.append(serialized[0] if len(serialized) == 1 else serialized)
                # Build node details per version and store them by message_id
                # (so the frontend can backfill subContent when switching to any version)
                for s in siblings:
                    nodes = build_nodes(s)
                    if nodes:
                        node_executions_map[str(s.id)] = nodes
        # Manual-intervention info: reuse the log system's aggregation logic,
        # same structure as the /public/share endpoint
        pending_intervention = AppLogService(self.db).build_pending_intervention_map(conv_id)
        return {
            "messages": items,
            "node_executions_map": node_executions_map,
            "pending_intervention": pending_intervention,
        }

    def _get_history_info(self, conversation_id: uuid.UUID) -> tuple[dict, list] | None:
        """Load conversation history (fallback used when from_message_id is empty).

        conv_vars comes from the latest terminal-status execution (completed /
        cancelled); conv_messages walks the parent chain of the latest message to
        get the current branch context.
        """
        # conv_vars from the latest terminal execution
        conv_vars: dict[str, Any] = {}
        result = self.db.execute(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.conversation_id == conversation_id,
                WorkflowExecution.status.in_(["completed", "cancelled"]),
            )
            .order_by(desc(WorkflowExecution.started_at))
            .limit(10)
        )
        for latest_execution in result.scalars():
            if isinstance(latest_execution.output_data, dict):
                variables = latest_execution.output_data.get("variables", {}) or {}
                conv_vars = variables.get("conv", {}) if isinstance(variables, dict) else {}
                if conv_vars:
                    break

        # conv_messages by walking the latest message's parent chain
        from ..models import Message as MessageModel
        latest = self.db.scalars(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.is_deleted.is_not(True),
            )
            .order_by(MessageModel.created_at.desc())
            .limit(1)
        ).first()
        if not latest:
            return None
        # _trace_context_chain excludes end_msg itself, so latest (= the previous
        # round's AI reply) would be dropped, leaving the LLM node history without
        # the previous assistant reply. Append latest itself, matching
        # _get_history_from_message(:4631-4633).
        conv_messages = self._trace_context_chain(latest)
        if latest.role in ("user", "assistant"):
            conv_messages.append({"role": latest.role, "content": latest.content})
        return conv_vars, conv_messages

    async def _trace_context_chain_async(self, db: AsyncSession, end_msg) -> list[dict[str, Any]]:
        from ..models import Message as MessageModel

        chain: list[MessageModel] = []
        visited: set[uuid.UUID] = set()
        cur = end_msg
        while cur is not None and cur.parent_message_id:
            if cur.id in visited:
                break
            visited.add(cur.id)
            parent = await db.get(MessageModel, cur.parent_message_id)
            if parent is None or parent.is_deleted:
                break
            chain.append(parent)
            cur = parent
        chain.reverse()
        return [
            {"role": m.role, "content": m.content}
            for m in chain if m.role in ("user", "assistant")
        ]

    async def _get_history_info_async(self, conversation_id: uuid.UUID) -> tuple[dict, list] | None:
        """Read conversation history, falling back to the completed-execution
        snapshot while messages are being persisted asynchronously."""
        conv_vars: dict[str, Any] = {}
        execution_messages: list[dict[str, str]] = []
        snapshot_execution_id: str | None = None
        from ..models import Message as MessageModel

        def normalize_completed_messages(messages: Any) -> list[dict[str, str]]:
            normalized: list[dict[str, str]] = []
            if not isinstance(messages, list):
                return normalized
            for item in messages:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                # Files appear as standalone user content blocks; the text user
                # message is still kept in the adjacent entry.
                if role in ("user", "assistant") and isinstance(content, str):
                    normalized.append({"role": role, "content": content})
            while normalized and normalized[-1]["role"] != "assistant":
                normalized.pop()
            return normalized

        async with get_async_db_context() as db:
            result = await db.execute(
                select(WorkflowExecution)
                .where(
                    WorkflowExecution.conversation_id == conversation_id,
                    WorkflowExecution.status.in_(["completed", "cancelled"]),
                )
                .order_by(desc(WorkflowExecution.started_at))
                .limit(10)
            )
            executions = list(result.scalars().all())
            latest_terminal = executions[0] if executions else None
            if (
                latest_terminal is not None
                and latest_terminal.status == "completed"
                and isinstance(latest_terminal.output_data, dict)
            ):
                execution_messages = normalize_completed_messages(
                    latest_terminal.output_data.get("messages", [])
                )
                if execution_messages:
                    snapshot_execution_id = str(latest_terminal.execution_id)

            for latest_execution in executions:
                if not isinstance(latest_execution.output_data, dict):
                    continue
                variables = latest_execution.output_data.get("variables", {}) or {}
                candidate_vars = variables.get("conv", {}) if isinstance(variables, dict) else {}
                if isinstance(candidate_vars, dict) and candidate_vars:
                    conv_vars = candidate_vars
                    break

            result = await db.execute(
                select(MessageModel)
                .where(
                    MessageModel.conversation_id == conversation_id,
                    MessageModel.is_deleted.is_not(True),
                )
                .order_by(MessageModel.created_at.desc())
                .limit(1)
            )
            latest = result.scalar_one_or_none()
            persisted_messages: list[dict[str, str]] = []
            persisted_execution_id: str | None = None
            if latest:
                persisted_messages = await self._trace_context_chain_async(db, latest)
                if latest.role in ("user", "assistant"):
                    persisted_messages.append({"role": latest.role, "content": latest.content})
                persisted_messages = normalize_completed_messages(persisted_messages)
                if latest.role == "assistant" and isinstance(latest.meta_data, dict):
                    execution_id = latest.meta_data.get("execution_id")
                    persisted_execution_id = str(execution_id) if execution_id else None

            # workflow_end may fire before BatchPersistQueue commits the messages.
            # When the latest completed execution has not yet shown up in
            # assistant.meta_data.execution_id, use its snapshot as the full
            # history of the current branch; cancelled snapshots do not
            # participate in the fallback, to avoid treating an intermediate
            # assistant message as a completed reply.
            if execution_messages and snapshot_execution_id != persisted_execution_id:
                conv_messages = execution_messages
            else:
                conv_messages = persisted_messages
            if not conv_messages and not conv_vars:
                return None
            return conv_vars, conv_messages



    def _get_history_from_message(self, message_id: str | None) -> tuple[dict, list] | None:
        """Build the history context starting from a given branch message
        ("continue chatting from a branch").

        Walks the parent_message_id chain back to the root to get the branch's
        full context (this round's user plus earlier rounds), excluding other
        retry versions from the same round. conv_vars comes from the execution
        corresponding to this branch.
        """
        if not message_id:
            return None
        from ..models import Message as MessageModel
        try:
            branch_msg = self.db.get(MessageModel, uuid.UUID(message_id))
        except (ValueError, TypeError):
            return None
        if not branch_msg:
            return None

        # conv_vars from the execution corresponding to this branch
        conv_vars: dict[str, Any] = {}
        exec_id = (branch_msg.meta_data or {}).get("execution_id") if branch_msg.meta_data else None
        if exec_id:
            execution = self.execution_repo.get_by_execution_id(str(exec_id))
            if execution and isinstance(execution.output_data, dict):
                variables = execution.output_data.get("variables", {}) or {}
                conv_vars = variables.get("conv", {}) if isinstance(variables, dict) else {}

        # conv_messages by walking the parent chain, appending branch_msg itself
        # (the preceding assistant belongs to the current branch context)
        conv_messages = self._trace_context_chain(branch_msg)
        if branch_msg.role in ("user", "assistant"):
            conv_messages.append({"role": branch_msg.role, "content": branch_msg.content})
        return conv_vars, conv_messages

    async def _get_history_from_message_async(self, message_id: str | None) -> tuple[dict, list] | None:
        if not message_id:
            return None

        from ..models import Message as MessageModel

        try:
            branch_message_id = uuid.UUID(message_id)
        except (ValueError, TypeError):
            return None

        async with get_async_db_context() as db:
            branch_msg = await db.get(MessageModel, branch_message_id)
            if not branch_msg:
                return None

            conv_vars: dict[str, Any] = {}
            exec_id = (branch_msg.meta_data or {}).get("execution_id") if branch_msg.meta_data else None
            if exec_id:
                result = await db.execute(
                    select(WorkflowExecution).where(WorkflowExecution.execution_id == str(exec_id)).limit(1)
                )
                execution = result.scalar_one_or_none()
                if execution and isinstance(execution.output_data, dict):
                    variables = execution.output_data.get("variables", {}) or {}
                    conv_vars = variables.get("conv", {}) if isinstance(variables, dict) else {}

            conv_messages = await self._trace_context_chain_async(db, branch_msg)
            if branch_msg.role in ("user", "assistant"):
                conv_messages.append({"role": branch_msg.role, "content": branch_msg.content})
            return conv_vars, conv_messages



    # ==================== Workflow execution ====================

    @bind_usage("app", "app_id")
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
            regenerate_mode: bool = False,
            regenerate_history: Optional[tuple] = None,
            prepared_memory_storage_type: str | None = None,
            prepared_user_rag_memory_id: str | None = None,
            skip_save: bool = False,
    ):
        """Run the workflow

        Args:
            release_id: release ID
            workspace_id: workspace ID
            config: config
            payload:
            app_id: app ID
            regenerate_mode: regenerate mode — skips the annotation-hit / input
                moderation short-circuits and skips message persistence
                (WorkflowService.regenerate saves the versioned messages itself).
            regenerate_history: history context injected on regenerate
                {"conv": {...}, "conv_messages": [...]}, overriding the default
                history read from the latest execution.

        Returns:
            Execution result (non-streaming)

        Raises:
            BusinessException: raised when the config is missing or execution fails
        """
        # 1. Get the workflow config
        if not config:
            config = await self.get_workflow_config_snapshot_async(app_id)
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

        # Resolve variable values of file type in the start node
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

        has_existing_conversation = bool(payload.conversation_id)
        # Convert conversation_id to UUID
        conversation_id_uuid = uuid.UUID(payload.conversation_id) if payload.conversation_id else None
        # skip_save semantics: pure functional execution (e.g. workflow-as-tool);
        # no conversation is created and no messages are persisted
        conversation_id_uuid = await self._ensure_conversation_async(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=payload.user_id,
            conversation_id=conversation_id_uuid,
            enable_conversation=supports_conversation and not skip_save,
        )
        if conversation_id_uuid:
            payload.conversation_id = str(conversation_id_uuid)
            input_data["conversation_id"] = str(conversation_id_uuid)

        # Check annotation hit — before creating the workflow execution; on a
        # hit, return immediately and skip the whole workflow.
        # The regenerate scenario does not short-circuit: it must run the full
        # workflow to produce a new version reply.
        annotation_match = None
        if supports_conversation and payload.message and not regenerate_mode:
            annotation_match = await self._check_annotation_match_async(
                app_id, payload.message, source=source or HitLogSource.CONSOLE
            )
        if annotation_match:
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()
            prev_messages = []
            history = await self._get_history_info_async(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                _, prev_messages = history
            if conversation_id_uuid and not skip_save:
                await self._add_message_async(
                    message_id=user_message_id,
                    conversation_id=conversation_id_uuid,
                    role="user",
                    content=payload.message,
                    meta_data={"files": []},
                )
                await self._add_message_async(
                    message_id=message_id,
                    conversation_id=conversation_id_uuid,
                    role="assistant",
                    content=annotation_match["answer"],
                    meta_data={"usage": {}},
                )

            # Create a WorkflowExecution record for log display
            execution = await self._create_execution_async(
                workflow_config_id=config.id,
                app_id=app_id,
                trigger_type=trigger_type,
                triggered_by=None,
                conversation_id=conversation_id_uuid,
                input_data=input_data,
                release_id=release_id,
                meta_data={
                    "trigger_id": trigger_id,
                    "trigger_meta": trigger_meta or {},
                    "external_event_id": None,
                },
            )
            output_messages = prev_messages + [
                {"role": "user", "content": payload.message},
                {"role": "assistant", "content": annotation_match["answer"]}
            ]
            await self._patch_execution_async(
                execution.execution_id,
                status="completed",
                output_data={
                    "answer": annotation_match["answer"],
                    "messages": output_messages,
                    "annotation_hit": {
                        "annotation_id": str(annotation_match["annotation_id"]),
                        "similarity": annotation_match["similarity"],
                        "question": annotation_match["question"],
                    }
                },
                token_usage={},
            )

            return {
                "status": "completed",
                "answer": annotation_match["answer"],
                "messages": output_messages,
                "conversation_id": str(conversation_id_uuid),
                "user_message_id": str(user_message_id),
                "token_usage": {},
                "elapsed_time": 0,
                "annotation_hit": {
                    "annotation_id": str(annotation_match["annotation_id"]),
                    "similarity": annotation_match["similarity"],
                    "question": annotation_match["question"],
                }
            }

        # 1.5 Input moderation (conversational workflows only, AppType.WORKFLOW)
        # The regenerate scenario skips the moderation short-circuit, so a
        # preset response does not replace the regeneration run
        sensitive_cfg = feature_configs.get("sensitive_word_avoidance", {})
        if payload.message and sensitive_cfg.get("enabled") and not regenerate_mode:
            from ..moderation.input_moderation import InputModeration
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
                user_message_id = uuid.uuid4()
                if conversation_id_uuid and not skip_save:
                    await self._add_message_async(
                        message_id=user_message_id,
                        conversation_id=conversation_id_uuid,
                        role="user",
                        content=payload.message,
                        meta_data={"files": []},
                    )
                    await self._add_message_async(
                        message_id=message_id,
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=preset_response,
                        meta_data={"usage": {}, "moderation_flagged": True},
                    )
                execution = await self._create_execution_async(
                    workflow_config_id=config.id,
                    app_id=app_id,
                    trigger_type="manual",
                    triggered_by=None,
                    conversation_id=conversation_id_uuid,
                    input_data={"message": payload.message, "moderation_flagged": True},
                    release_id=release_id,
                    meta_data={
                        "trigger_id": trigger_id,
                        "trigger_meta": trigger_meta or {},
                        "external_event_id": None,
                    },
                )
                await self._patch_execution_async(
                    execution.execution_id,
                    status="completed",
                    output_data={"answer": preset_response, "moderation_flagged": True},
                )
                return {
                    "execution_id": execution.execution_id,
                    "status": "completed",
                    "output": preset_response,
                    "message": preset_response,
                    "message_id": str(message_id),
                    "user_message_id": str(user_message_id),
                    "conversation_id": str(conversation_id_uuid) if conversation_id_uuid else None,
                    "error_message": None,
                    "elapsed_time": 0,
                    "token_usage": {},
                    "citations": [],
                    "moderation_flagged": True,
                }

        # 2. Create the execution record
        execution = await self._create_execution_async(
            workflow_config_id=config.id,
            app_id=app_id,
            trigger_type=trigger_type,
            triggered_by=None,
            conversation_id=conversation_id_uuid,
            input_data=input_data,
            release_id=release_id,
            meta_data={
                "trigger_id": trigger_id,
                "trigger_meta": trigger_meta or {},
                "external_event_id": None,
                # Record the run source; WorkflowAsTool passes "tool", and the app
                # log excludes internal sub-calls based on it.
                "source": str(source or "workflow"),
            },
        )

        # 3. Build the workflow config dict
        workflow_config_dict = self._build_runtime_workflow_config_dict(
            app_id=app_id,
            workflow_config=config,
            features=feature_configs,
            runtime_options={"bypass_node_cache": True},
        )

        files = []
        # Whether this round's messages have been saved/enqueued (same guard
        # semantics as run_stream)
        messages_finalized = False
        try:
            files = await self._handle_file_input(payload.files)
            if prepared_memory_storage_type is not None:
                storage_type = prepared_memory_storage_type
                user_rag_memory_id = prepared_user_rag_memory_id or ""
            else:
                storage_type, user_rag_memory_id = await self._get_memory_store_info_async(workspace_id)
            input_data["files"] = files
            message_id = uuid.uuid4()
            user_message_id = None if regenerate_mode else uuid.uuid4()

            if regenerate_mode and regenerate_history:
                history = regenerate_history
            elif payload.from_message_id:
                history = await self._get_history_from_message_async(payload.from_message_id)
                if history is None and conversation_id_uuid:
                    history = await self._get_history_info_async(conversation_id_uuid)
            elif not has_existing_conversation:
                history = None
            else:
                history = await self._get_history_info_async(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                conv_vars, conv_messages = history
                input_data["conv"] = conv_vars
                input_data["conv_messages"] = conv_messages
            init_message_length = len(input_data.get("conv_messages", []))

            # Write the opening statement for a new conversation
            is_new_conversation = init_message_length == 0
            if is_new_conversation and not regenerate_mode:
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
                    if not skip_save:
                        await self._add_message_async(
                            conversation_id=conversation_id_uuid,
                            role="assistant",
                            content=statement,
                            meta_data={"suggested_questions": suggested_questions},
                        )
                    input_data["conv_messages"] = [{"role": "assistant", "content": statement}]
                    init_message_length = 1

            execution = await self._patch_execution_async(
                execution.execution_id,
                input_data=input_data,
                status="running",
            )

            result = await execute_workflow(
                workflow_config=workflow_config_dict,
                input_data=input_data,
                execution_id=execution.execution_id,
                workspace_id=str(workspace_id),
                user_id=payload.user_id,
                memory_storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id
            )

            # Output moderation (non-streaming)
            if result.get("status") == "completed" and sensitive_cfg.get("enabled"):
                outputs_enabled = sensitive_cfg.get("config", {}).get("outputs_config", {}).get("enabled", False)
                if outputs_enabled:
                    from ..moderation.output_moderation import OutputModeration
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

            # Update the execution result
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
                # Filter citations
                citations = result.get("citations", [])
                citation_cfg = feature_configs.get("citation", {})
                if isinstance(citation_cfg, dict) and citation_cfg.get("enabled"):
                    allow_download = citation_cfg.get("allow_download", False)
                    if allow_download:
                        from ..config import current_settings
                        for c in citations:
                            if c.get("document_id"):
                                c["download_url"] = f"{current_settings().file_local_server_url}/apps/citations/{c['document_id']}/download"
                    filtered_citations = citations
                else:
                    filtered_citations = []
                assistant_meta = {"usage": token_usage, "audio_url": None, "execution_id": execution.execution_id}
                if filtered_citations:
                    assistant_meta["citations"] = filtered_citations
                # Under regenerate_mode, regenerate() saves the versioned messages
                # itself; save_messages must not be enqueued here, or the
                # conversation would get an extra ghost message pair (and
                # streaming regeneration would hit a primary-key conflict on the
                # same message_id). Under skip_save (pure functional execution
                # such as workflow-as-tool) messages are likewise not persisted.
                if conversation_id_uuid and not regenerate_mode and not skip_save:
                    messages_finalized = True
                    _from_msg_id = await self._resolve_from_message_id_async(
                        payload.from_message_id,
                        conversation_id_uuid,
                    )
                    execution = await self._finalize_completed_execution_async(
                        execution_id=execution.execution_id,
                        conversation_id=conversation_id_uuid,
                        human_message=human_message,
                        human_meta=human_meta,
                        user_message_id=user_message_id,
                        from_message_id=_from_msg_id,
                        assistant_message_id=message_id,
                        assistant_message=assistant_message,
                        assistant_meta=assistant_meta,
                        workflow_output_data=result,
                        token_usage=token_usage.get("total_tokens", None),
                    )
                else:
                    execution = await self._patch_execution_async(
                        execution.execution_id,
                        status="completed",
                        output_data=result,
                        token_usage=token_usage.get("total_tokens", None),
                    )
                execution_record = await self._get_execution_async(execution.execution_id)
                if execution_record:
                    await self._persist_workflow_node_executions_async(execution_record, config, result)
                    await self._refresh_workflow_debug_state_from_execution_async(execution_record, workflow_config=config)

                logger.info(f"Workflow Run Success, "
                            f"execution_id: {execution.execution_id}, message count: {len(final_messages)}")
            else:
                await self._patch_execution_async(
                    execution.execution_id,
                    status="failed",
                    # Persist the full result (including node_outputs) on failure
                    # too, otherwise the app log cannot show per-node execution
                    # details of the failed run.
                    output_data=result,
                    error_message=result.get("error"),
                    error_node_id=result.get("error_node"),
                )
                execution_record = await self._get_execution_async(execution.execution_id)
                if execution_record:
                    await self._persist_workflow_node_executions_async(execution_record, config, result)
                    await self._refresh_workflow_debug_state_from_execution_async(execution_record, workflow_config=config)
                logger.error(f"Workflow Run Failed, execution_id: {execution.execution_id},"
                             f" error: {result.get('error')}")
                final_messages = result.get("messages", [])[init_message_length:]
                human_message, human_meta = self._extract_human_message_and_meta(
                    final_messages, payload.message or "", files
                )
                if not regenerate_mode and not skip_save:
                    messages_finalized = True
                    await self._save_failed_conversation_async(
                        conversation_id_uuid, message_id, human_message, human_meta, result.get("error") or ""
                    )
                filtered_citations = []

            response_data = {
                "execution_id": execution.execution_id,
                "status": result.get("status"),
                "output": result.get("output"),
                "message": result.get("output"),
                "message_id": str(message_id),
                "user_message_id": str(user_message_id) if user_message_id else None,
                "conversation_id": result.get("conversation_id") or (str(conversation_id_uuid) if conversation_id_uuid else None),
                "error_message": result.get("error"),
                "elapsed_time": result.get("elapsed_time"),
                "token_usage": result.get("token_usage"),
                "citations": filtered_citations,
            }

            if result.get("status") == "waiting_human":
                # Non-streaming execution does not support human intervention nodes.
                # Clean up checkpointer before raising to prevent memory leak.
                cp_thread_id = result.get("checkpoint_thread_id", "")
                if cp_thread_id:
                    from ..workflow.engine.graph_builder import remove_checkpointer
                    remove_checkpointer(cp_thread_id)
                from ..workflow.nodes.human_intervention.node import InterventionRegistry
                InterventionRegistry.cleanup(execution.execution_id)
                await self._patch_execution_async(
                    execution.execution_id,
                    status="failed",
                    output_data=result,
                    error_message="非流式执行不支持人工介入节点",
                    error_node_id=result.get("error_node"),
                )
                try:
                    execution_record = await self._get_execution_async(execution.execution_id)
                    if execution_record and execution_record.output_data:
                        await self._persist_workflow_node_executions_async(execution_record, config, execution_record.output_data)
                        await self._refresh_workflow_debug_state_from_execution_async(execution_record, workflow_config=config)
                except Exception as persist_err:
                    logger.warning(f"Failed to persist node executions on waiting_human: {persist_err}")
                raise BusinessException(
                    code=BizCode.BAD_REQUEST,
                    message="当前工作流包含人工介入节点，非流式执行不支持，请使用流式接口"
                )

            return response_data

        except BusinessException:
            raise
        except Exception as e:
            logger.error(f"工作流执行失败: execution_id={execution.execution_id}, error={e}", exc_info=True)
            await self._patch_execution_async(
                execution.execution_id,
                status="failed",
                error_message=str(e),
            )
            try:
                execution_record = await self._get_execution_async(execution.execution_id)
                if execution_record and execution_record.output_data:
                    await self._persist_workflow_node_executions_async(execution_record, config, execution_record.output_data)
                    await self._refresh_workflow_debug_state_from_execution_async(execution_record, workflow_config=config)
            except Exception as persist_err:
                logger.warning(f"Failed to persist node executions on run error: {persist_err}")
            human_message, human_meta = self._extract_human_message_and_meta([], payload.message or "", files)
            # If this round's messages were already enqueued successfully, do
            # not enqueue failure messages again, avoiding duplicate writes
            if not skip_save and not regenerate_mode and not messages_finalized:
                await self._save_failed_conversation_async(conversation_id_uuid, None, human_message, human_meta, str(e))
            raise BusinessException(
                code=BizCode.INTERNAL_ERROR,
                message=f"工作流执行失败: {str(e)}"
            )

    @bind_usage("app", "app_id")
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
            regenerate_mode: bool = False,
            regenerate_history: Optional[tuple] = None,
            regenerate_message_id: Optional[uuid.UUID] = None,
            preserved_execution: "WorkflowExecution | None" = None,
            prepared_memory_storage_type: str | None = None,
            prepared_user_rag_memory_id: str | None = None,
            skip_save: bool = False,
    ):
        """When `preserved_execution` is not None, skip the three
        `create_execution` calls inside this method and reuse the
        WorkflowExecution row the caller already created. Mainly used by the
        share-experience regenerate scenario to obtain the execution_id before
        yielding the `start` event."""
        """Run the workflow (streaming)

        Args:
            release_id: release id
            workspace_id:
            app_id: app ID
            payload: request object (contains message, variables, conversation_id, etc.)
            config: storage type (optional)
            public: whether published
            regenerate_mode: regenerate mode — skips the annotation-hit / input
                moderation short-circuits and skips message persistence
                (WorkflowService.regenerate_stream saves the versioned messages itself).
            regenerate_history: history context injected on regenerate, overriding
                the default history read from the latest execution.

        Yields:
            SSE-format streaming events

        Raises:
            BusinessException: raised when the config is missing or execution fails
        """
        request_started_at = time.perf_counter()
        before_execute_started_at = request_started_at
        before_execute_last_checkpoint_at = request_started_at
        stream_started_at: float | None = None
        first_message_at: float | None = None
        workflow_end_seen_at: float | None = None
        timing_execution_id = "pending"

        def _log_run_stream_timing(stage: str, **extra: Any) -> None:
            now = time.perf_counter()
            payload_items = {
                "stage": stage,
                "execution_id": timing_execution_id,
                "elapsed_ms": round((now - request_started_at) * 1000, 2),
            }
            if stream_started_at is not None:
                payload_items["stream_ms"] = round((now - stream_started_at) * 1000, 2)
            if first_message_at is not None:
                payload_items["post_first_chunk_ms"] = round((now - first_message_at) * 1000, 2)
            if workflow_end_seen_at is not None:
                payload_items["finalize_ms"] = round((now - workflow_end_seen_at) * 1000, 2)
            payload_items.update(extra)
            logger.info(
                "[TIMING] workflow.run_stream %s",
                " ".join(f"{k}={v}" for k, v in payload_items.items()),
            )

        def _log_before_execute_step(stage: str, **extra: Any) -> None:
            nonlocal before_execute_last_checkpoint_at
            now = time.perf_counter()
            payload_items = {
                "stage": stage,
                "execution_id": timing_execution_id,
                "before_execute_ms": round((now - before_execute_started_at) * 1000, 2),
                "step_ms": round((now - before_execute_last_checkpoint_at) * 1000, 2),
            }
            payload_items.update(extra)
            logger.info(
                "[TIMING] workflow.run_stream %s",
                " ".join(f"{k}={v}" for k, v in payload_items.items()),
            )
            before_execute_last_checkpoint_at = now

        # 1. Get the workflow config
        if not config:
            config = await self.get_workflow_config_snapshot_async(app_id)
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
        _log_before_execute_step(
            "before_execute_config_ready",
            supports_conversation=supports_conversation,
            has_release_id=release_id is not None,
        )

        # Resolve variable values of file type in the start node
        start_node_vars = self.get_start_node_variables({"nodes": config.nodes})
        resolved_variables = await self._resolve_start_node_file_variables(
            start_node_vars, payload.variables or {}
        )
        _log_before_execute_step(
            "before_execute_input_resolved",
            start_node_var_count=len(start_node_vars or []),
            file_count=len(payload.files or []),
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

        has_existing_conversation = bool(payload.conversation_id)
        # Convert conversation_id to UUID
        conversation_id_uuid = uuid.UUID(payload.conversation_id) if payload.conversation_id else None
        # skip_save semantics: pure functional execution (e.g. workflow-as-tool);
        # no conversation is created and no messages are persisted
        conversation_id_uuid = await self._ensure_conversation_async(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=payload.user_id,
            conversation_id=conversation_id_uuid,
            enable_conversation=supports_conversation and not skip_save,
        )
        if conversation_id_uuid:
            payload.conversation_id = str(conversation_id_uuid)
            input_data["conversation_id"] = str(conversation_id_uuid)
        _log_before_execute_step(
            "before_execute_conversation_ready",
            conversation_id=str(conversation_id_uuid) if conversation_id_uuid else None,
        )

        # Check annotation hit — before creating the workflow execution; on a
        # hit, return immediately and skip the whole workflow.
        # The regenerate scenario does not short-circuit: it must run the full
        # workflow to produce a new version reply.
        annotation_match = None
        if supports_conversation and payload.message and not regenerate_mode:
            annotation_match = await self._check_annotation_match_async(
                app_id, payload.message, source=source or HitLogSource.CONSOLE
            )
        if annotation_match:
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()
            prev_messages = []
            history = await self._get_history_info_async(conversation_id_uuid) if conversation_id_uuid else None
            if history:
                _, prev_messages = history
            if conversation_id_uuid and not skip_save:
                await self._add_message_async(
                    message_id=user_message_id,
                    conversation_id=conversation_id_uuid,
                    role="user",
                    content=payload.message,
                    meta_data={"files": []},
                )
                await self._add_message_async(
                    message_id=message_id,
                    conversation_id=conversation_id_uuid,
                    role="assistant",
                    content=annotation_match["answer"],
                    meta_data={"usage": {}},
                )

            # Create a WorkflowExecution record for log display
            if preserved_execution is not None:
                execution = preserved_execution
            else:
                execution = await self._create_execution_async(
                    workflow_config_id=config.id,
                    app_id=app_id,
                    trigger_type=trigger_type,
                    triggered_by=None,
                    conversation_id=conversation_id_uuid,
                    input_data=input_data,
                    release_id=release_id,
                    meta_data={
                        "trigger_id": trigger_id,
                        "trigger_meta": trigger_meta or {},
                        "external_event_id": None,
                    },
                )
            timing_execution_id = execution.execution_id
            output_messages = prev_messages + [
                {"role": "user", "content": payload.message},
                {"role": "assistant", "content": annotation_match["answer"]}
            ]
            execution = await self._patch_execution_async(
                execution.execution_id,
                status="completed",
                output_data={
                "answer": annotation_match["answer"],
                "messages": output_messages,
                "annotation_hit": {
                    "annotation_id": str(annotation_match["annotation_id"]),
                    "similarity": annotation_match["similarity"],
                    "question": annotation_match["question"],
                }
                },
                token_usage={},
            )

            start_internal_event = {
                "event": "workflow_start",
                "data": {
                    "conversation_id": str(conversation_id_uuid),
                    "message_id": str(message_id),
                    "user_message_id": str(user_message_id)
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

        # 1.5 Input moderation (conversational workflows only, AppType.WORKFLOW)
        # The regenerate scenario skips the moderation short-circuit, so a
        # preset response does not replace the regeneration run
        sensitive_cfg = feature_configs.get("sensitive_word_avoidance", {})
        if payload.message and sensitive_cfg.get("enabled") and not regenerate_mode:
            from ..moderation.input_moderation import InputModeration
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
                user_message_id = uuid.uuid4()
                if conversation_id_uuid and not skip_save:
                    await self._add_message_async(
                        message_id=user_message_id,
                        conversation_id=conversation_id_uuid,
                        role="user",
                        content=payload.message,
                        meta_data={"files": []},
                    )
                    await self._add_message_async(
                        message_id=message_id,
                        conversation_id=conversation_id_uuid,
                        role="assistant",
                        content=preset_response,
                        meta_data={"usage": {}, "moderation_flagged": True},
                    )
                if preserved_execution is not None:
                    execution = preserved_execution
                else:
                    execution = await self._create_execution_async(
                        workflow_config_id=config.id,
                        app_id=app_id,
                        trigger_type="manual",
                        triggered_by=None,
                        conversation_id=conversation_id_uuid,
                        input_data={"message": payload.message, "moderation_flagged": True},
                        release_id=release_id,
                        meta_data={
                            "trigger_id": trigger_id,
                            "trigger_meta": trigger_meta or {},
                            "external_event_id": None,
                        },
                    )
                timing_execution_id = execution.execution_id
                execution = await self._patch_execution_async(
                    execution.execution_id,
                    status="completed",
                    output_data={"answer": preset_response, "moderation_flagged": True},
                )

                start_internal_event = {
                    "event": "workflow_start",
                    "data": {
                        "conversation_id": str(conversation_id_uuid),
                        "message_id": str(message_id),
                        "user_message_id": str(user_message_id)
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

        execution = preserved_execution

        # 3. Build the workflow config dict
        workflow_config_dict = self._build_runtime_workflow_config_dict(
            app_id=app_id,
            workflow_config=config,
            features=feature_configs,
            runtime_options={"bypass_node_cache": True},
        )
        _log_before_execute_step("before_execute_runtime_config_ready")

        _lock_key: str | None = None
        _lock_value: str | None = None
        _lock_degraded = False
        _last_event_data: dict | None = None
        # Whether this round's conversation messages (the success or failure
        # pair) have been saved/enqueued. Once True, no later branch may enqueue
        # save_messages / save_failed_message for the same round, or
        # BatchPersistQueue would hit a messages primary-key conflict on the
        # same message id.
        messages_finalized = False
        workflow_event_stream = None
        accumulated_content = ""
        output_contents: dict[str, str] = {}
        output_order: list[str] = []
        optimistic_checkpoints: dict[tuple[str, int | None], dict[str, Any]] = {}
        output_moderation = None

        try:
            files = await self._handle_file_input(payload.files)
            if prepared_memory_storage_type is not None:
                storage_type = prepared_memory_storage_type
                user_rag_memory_id = prepared_user_rag_memory_id or ""
            else:
                storage_type, user_rag_memory_id = await self._get_memory_store_info_async(workspace_id)
            input_data["files"] = files
            _log_before_execute_step(
                "before_execute_files_ready",
                file_count=len(files or []),
                storage_type=storage_type,
                has_user_rag_memory_id=bool(user_rag_memory_id),
            )
            history_source = "none"
            if regenerate_mode and regenerate_history:
                history = regenerate_history
                history_source = "regenerate"
            elif payload.from_message_id:
                history = await self._get_history_from_message_async(payload.from_message_id)
                history_source = "from_message"
                if history is None and conversation_id_uuid:
                    history = await self._get_history_info_async(conversation_id_uuid)
                    history_source = "conversation_fallback"
            elif not has_existing_conversation:
                history = None
                history_source = "new_conversation_skip"
            else:
                history = await self._get_history_info_async(conversation_id_uuid) if conversation_id_uuid else None
                history_source = "conversation" if conversation_id_uuid else "none"
            if history:
                conv_vars, conv_messages = history
                input_data["conv"] = conv_vars
                input_data["conv_messages"] = conv_messages
            init_message_length = len(input_data.get("conv_messages", []))
            _log_before_execute_step(
                "before_execute_history_ready",
                history_message_count=init_message_length,
                history_source=history_source,
            )

            message_id = regenerate_message_id or uuid.uuid4()
            user_message_id = None if regenerate_mode else uuid.uuid4()
            _cycle_items: dict[str, list] = {}
            intervention_list = []

            # Write the opening statement for a new conversation
            is_new_conversation = init_message_length == 0
            if is_new_conversation and not regenerate_mode:
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
                    if not skip_save:
                        await self._add_message_async(
                            conversation_id=conversation_id_uuid,
                            role="assistant",
                            content=statement,
                            meta_data={"suggested_questions": suggested_questions},
                        )
                    input_data["conv_messages"] = [{"role": "assistant", "content": statement}]
                    init_message_length = 1
            _log_before_execute_step(
                "before_execute_opening_ready",
                is_new_conversation=is_new_conversation,
            )

            output_moderation = None
            llm_node_ids: set[str] = set()
            if sensitive_cfg.get("enabled"):
                outputs_enabled = sensitive_cfg.get("config", {}).get("outputs_config", {}).get("enabled", False)
                if outputs_enabled:
                    from ..moderation.output_moderation import OutputModeration
                    output_moderation = OutputModeration(
                        app_id=str(app_id),
                        tenant_id=str(workspace_id),
                        moderation_type=sensitive_cfg.get("type", "keywords"),
                        moderation_config=sensitive_cfg,
                    )
                    for node in (config.nodes or []):
                        if node.get("type") == "llm":
                            llm_node_ids.add(node.get("id", ""))
            _log_before_execute_step(
                "before_execute_output_moderation_ready",
                moderation_enabled=output_moderation is not None,
                llm_node_count=len(llm_node_ids),
            )

            if execution is not None:
                execution = await self._patch_execution_async(
                    execution.execution_id,
                    input_data=input_data,
                    status="running",
                )
            else:
                execution = self._build_transient_execution_ref(
                    workflow_config_id=config.id,
                    app_id=app_id,
                    trigger_type=trigger_type,
                    triggered_by=None,
                    conversation_id=conversation_id_uuid,
                    input_data=self._build_persisted_execution_input_data(input_data),
                    release_id=release_id,
                    meta_data={
                        "trigger_id": trigger_id,
                        "trigger_meta": trigger_meta or {},
                        "external_event_id": None,
                    },
                    status="running",
                )
                await self._store_runtime_execution_snapshot_async(execution)
                logger.info(
                    f">>>>>>> TIMING_TRACE execution_started execution_id={execution.execution_id} "
                    f"started_at={execution.started_at.isoformat() if execution.started_at else 'None'} "
                    f"(transient)"
                )
            timing_execution_id = execution.execution_id
            _log_before_execute_step(
                "before_execute_execution_ready",
                preserved_execution=preserved_execution is not None,
                status="running",
            )

            # ── Conversation execution lock ──
            if conversation_id_uuid:
                _lock_key = f"workflow:exec_lock:{conversation_id_uuid}"
                _lock_value = f"{execution.execution_id}:{time.time()}"
                acquired = False
                try:
                    acquired = (await asyncio.wait_for(
                        aio_redis.set(_lock_key, _lock_value, nx=True, ex=120),
                        timeout=2.0,
                    )) or False
                except Exception as redis_exc:
                    logger.warning(
                        "Redis lock acquisition failed, degrading to no-lock mode: %s",
                        redis_exc,
                    )
                    acquired = True
                    _lock_degraded = True
                    _lock_key = None

                if not acquired:
                    logger.info(
                        "Lock held by another execution, waiting: conversation_id=%s new_execution=%s",
                        conversation_id_uuid, execution.execution_id,
                    )
                    for _ in range(10):
                        await asyncio.sleep(0.2)
                        try:
                            acquired = (await asyncio.wait_for(
                                aio_redis.set(_lock_key, _lock_value, nx=True, ex=120),
                                timeout=1.0,
                            )) or False
                        except Exception:
                            acquired = True
                            _lock_degraded = True
                            _lock_key = None
                            break
                        if acquired:
                            break

                    if not acquired:
                        logger.warning(
                            "Lock wait timeout, using fallback vars: conversation_id=%s execution_id=%s",
                            conversation_id_uuid, execution.execution_id,
                        )
                        # Try to cancel the previous execution (A); read A's
                        # execution_id from the Redis lock value
                        try:
                            holder_value = await aio_redis.get(_lock_key)
                            if holder_value:
                                holder_exec_id = (holder_value or "").split(":")[0]
                                if holder_exec_id and holder_exec_id != execution.execution_id:
                                    await self._patch_execution_async(
                                        holder_exec_id, status="cancelled",
                                    )
                                    logger.info(
                                        "Cancelled previous execution after lock timeout: "
                                        "previous_execution=%s new_execution=%s",
                                        holder_exec_id, execution.execution_id,
                                    )
                        except Exception as cancel_err:
                            logger.warning(
                                "Failed to cancel previous execution after lock timeout: %s",
                                cancel_err,
                            )
                        _lock_key = None
                    elif not _lock_degraded:
                        # After acquiring the lock, refresh both variables and
                        # messages; the previous round may have just finished and released the lock.
                        try:
                            refetched = await self._get_history_info_async(conversation_id_uuid)
                            if refetched:
                                refetched_vars, refetched_messages = refetched
                                input_data["conv"] = refetched_vars
                                input_data["conv_messages"] = refetched_messages
                                init_message_length = len(refetched_messages)
                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    input_data=input_data,
                                )
                        except Exception:
                            pass

            # One workflow execution owns one assistant message. Individual
            # Answer/End outputs are kept as logical responses inside that
            # message and are addressed by node_id in first-arrival order.
            # Optimistic transactions are isolated by (source node, attempt).

            def ensure_output_order(
                    node_id: str,
                    *,
                    order: Optional[list[str]] = None,
            ) -> list[str]:
                target_order = output_order if order is None else order
                if node_id not in target_order:
                    target_order.append(node_id)
                return target_order

            def stream_key(source_node_id: str, attempt: int | None) -> tuple[str, int | None]:
                return source_node_id, attempt

            def pop_optimistic_checkpoint(
                    source_node_id: str,
                    attempt: int | None,
            ) -> dict[str, Any] | None:
                key = stream_key(source_node_id, attempt)
                checkpoint = optimistic_checkpoints.pop(key, None)
                if checkpoint is None and attempt is None:
                    matching = [
                        candidate for candidate in optimistic_checkpoints
                        if candidate[0] == source_node_id
                    ]
                    if len(matching) == 1:
                        checkpoint = optimistic_checkpoints.pop(matching[0], None)
                return checkpoint

            def rebuild_multi_content(
                    contents: Optional[dict[str, str]] = None,
                    order: Optional[list[str]] = None,
            ) -> str:
                current_contents = output_contents if contents is None else contents
                current_order = output_order if order is None else order
                return "\n".join(
                    current_contents[node_id]
                    for node_id in current_order
                    if current_contents.get(node_id)
                )

            def ordered_outputs() -> list[dict[str, Any]]:
                return [
                    {
                        "node_id": output_node_id,
                        "content": output_contents[output_node_id],
                        "status": "completed",
                    }
                    for output_node_id in output_order
                    if output_contents.get(output_node_id)
                ]

            queued_intervention_events = []

            moderation_flagged = False
            active_llm_nodes: set[str] = set()  # LLM nodes with node_start but no node_end yet
            stream_started_at = time.perf_counter()
            _log_run_stream_timing(
                "before_execute",
                conversation_id=str(conversation_id_uuid) if conversation_id_uuid else None,
            )

            workflow_event_stream = execute_workflow_stream(
                workflow_config=workflow_config_dict,
                input_data=input_data,
                execution_id=execution.execution_id,
                workspace_id=str(workspace_id),
                user_id=payload.user_id,
                memory_storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
            )
            async for event in workflow_event_stream:
                _last_event_data = event.get("data") or {}
                event_type = event.get("event")
                event_data = event.get("data", {})

                # Internal optimistic-stream controls are consumed here. Clients
                # continue to see only message/message_replace events.
                if event_type == "stream_rollback":
                    source_node_id = event_data.get("source_node_id")
                    checkpoint = pop_optimistic_checkpoint(
                        source_node_id,
                        event_data.get("attempt"),
                    )
                    if checkpoint is not None:
                        output_node_id = checkpoint.get("output_node_id")
                        if checkpoint.get("scoped") and output_node_id:
                            restored_content = checkpoint.get("content", "") or ""
                            if checkpoint.get("existed"):
                                output_contents[output_node_id] = restored_content
                            else:
                                output_contents.pop(output_node_id, None)
                            accumulated_content = rebuild_multi_content()
                            replace_content = restored_content
                            replace_node_id = output_node_id
                        else:
                            accumulated_content = checkpoint.get("content", "") or ""
                            output_contents = dict(checkpoint.get("outputs", {}))
                            replace_content = accumulated_content
                            replace_node_id = None
                        if output_moderation:
                            output_moderation.reset(accumulated_content)
                        # Moderated provisional chunks were never published, so
                        # their rollback is internal-only and must not create an
                        # empty public answer bucket.
                        if checkpoint.get("published", True):
                            replace_event = self._emit(public, {
                                "event": "message_replace",
                                "data": {
                                    "message_id": str(message_id),
                                    "node_id": replace_node_id,
                                    "content": replace_content,
                                },
                            })
                            if replace_event:
                                yield replace_event
                    continue
                if event_type == "stream_reconcile":
                    source_node_id = event_data.get("source_node_id")
                    checkpoint = pop_optimistic_checkpoint(
                        source_node_id,
                        event_data.get("attempt"),
                    )
                    if checkpoint is None:
                        continue
                    canonical_content = event_data.get("content", "") or ""
                    output_node_id = checkpoint.get("output_node_id")
                    if checkpoint.get("scoped") and output_node_id:
                        stable_output = checkpoint.get("content", "") or ""
                        provisional_output = output_contents.get(output_node_id, "") or ""
                        ensure_output_order(output_node_id)
                        if output_moderation:
                            # Provisional chunks were withheld. Route only the
                            # canonical span through the normal moderation path.
                            output_contents[output_node_id] = stable_output
                            stable_content = rebuild_multi_content()
                            accumulated_content = stable_content
                            output_moderation.reset(stable_content)
                            event = {
                                "event": "message",
                                "data": {
                                    "content": canonical_content,
                                    "node_id": output_node_id,
                                },
                            }
                            event_type = "message"
                            event_data = event["data"]
                        else:
                            reconciled_output = stable_output + canonical_content
                            if provisional_output != reconciled_output:
                                logger.error(
                                    "Optimistic stream invariant violated: source_node_id=%s "
                                    "output_node_id=%s provisional_length=%s canonical_length=%s",
                                    source_node_id,
                                    output_node_id,
                                    len(provisional_output),
                                    len(reconciled_output),
                                )
                                output_contents[output_node_id] = reconciled_output
                                accumulated_content = rebuild_multi_content()
                                correction = self._emit(public, {
                                    "event": "message_replace",
                                    "data": {
                                        "message_id": str(message_id),
                                        "node_id": output_node_id,
                                        "content": reconciled_output,
                                    },
                                })
                                if correction:
                                    yield correction
                            else:
                                output_contents[output_node_id] = provisional_output
                                accumulated_content = rebuild_multi_content()
                            continue
                    else:
                        stable_content = checkpoint.get("content", "") or ""
                        provisional_content = accumulated_content
                        if output_moderation:
                            output_contents = dict(checkpoint.get("outputs", {}))
                            accumulated_content = stable_content
                            output_moderation.reset(stable_content)
                            event = {
                                "event": "message",
                                "data": {"content": canonical_content},
                            }
                            event_type = "message"
                            event_data = event["data"]
                        else:
                            reconciled_content = stable_content + canonical_content
                            if provisional_content != reconciled_content:
                                logger.error(
                                    "Unscoped optimistic stream invariant violated: "
                                    "source_node_id=%s provisional_length=%s canonical_length=%s",
                                    source_node_id,
                                    len(provisional_content),
                                    len(reconciled_content),
                                )
                                accumulated_content = reconciled_content
                                correction = self._emit(public, {
                                    "event": "message_replace",
                                    "data": {
                                        "message_id": str(message_id),
                                        "node_id": None,
                                        "content": reconciled_content,
                                    },
                                })
                                if correction:
                                    yield correction
                            else:
                                accumulated_content = provisional_content
                            continue

                provisional_node_id = (
                    event_data.get("provisional_node_id")
                    if event_type == "message" else None
                )
                if provisional_node_id:
                    attempt = event_data.get("attempt")
                    checkpoint_key = stream_key(provisional_node_id, attempt)
                    output_node_id = event_data.get("node_id")
                    if output_node_id:
                        ensure_output_order(output_node_id)
                        optimistic_checkpoints.setdefault(
                            checkpoint_key,
                            {
                                "source_node_id": provisional_node_id,
                                "attempt": attempt,
                                "scoped": True,
                                "output_node_id": output_node_id,
                                "content": output_contents.get(output_node_id, ""),
                                "existed": output_node_id in output_contents,
                                "published": not bool(output_moderation),
                            },
                        )
                    else:
                        optimistic_checkpoints.setdefault(
                            checkpoint_key,
                            {
                                "source_node_id": provisional_node_id,
                                "attempt": attempt,
                                "scoped": False,
                                "output_node_id": output_node_id,
                                "content": accumulated_content,
                                "outputs": dict(output_contents),
                                "published": not bool(output_moderation),
                            },
                        )
                    # Preserve the existing moderation guarantee: tentative text
                    # is never exposed before it becomes canonical SUCCESS output.
                    if output_moderation:
                        continue

                # After moderation triggers, skip all subsequent message events
                # (no more model streaming text is emitted to the client)
                if moderation_flagged and event_type == "message":
                    continue

                # message events: moderate before yielding; on a keyword hit,
                # immediately emit message_replace and end the stream
                if event_type == "message" and output_moderation and not moderation_flagged:
                    if first_message_at is None:
                        first_message_at = time.perf_counter()
                        _log_run_stream_timing("first_message_chunk", content_len=len(event_data.get("content", "") or ""))
                    chunk = event_data.get("content", "") or ""
                    output_node_id = event_data.get("node_id")
                    if output_node_id:
                        candidate_outputs = dict(output_contents)
                        candidate_order = list(output_order)
                        ensure_output_order(
                            output_node_id,
                            order=candidate_order,
                        )
                        candidate_outputs[output_node_id] = (
                            candidate_outputs.get(output_node_id, "") + chunk
                        )
                        candidate_content = rebuild_multi_content(
                            candidate_outputs,
                            candidate_order,
                        )
                    else:
                        candidate_content = accumulated_content + chunk
                    if output_moderation.replace_and_check(candidate_content):
                        moderation_flagged = True
                        yield {"event": "message_replace",
                               "data": {
                                   "message_id": str(message_id),
                                   # Moderation replaces the whole assistant reply,
                                   # not one answer bucket.
                                   "node_id": None,
                                   "content": output_moderation.preset_response,
                               }}

                        # After moderation triggers, immediately save the
                        # conversation, update the execution status, synthesize
                        # the end event, and exit the loop
                        preset_response = output_moderation.preset_response
                        human_message, human_meta = self._extract_human_message_and_meta(
                            [], payload.message or "", files
                        )
                        # Update the execution status to completed and mark
                        # moderation as triggered
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
                        if conversation_id_uuid and not skip_save:
                            await self._add_message_async(
                                message_id=user_message_id,
                                conversation_id=conversation_id_uuid,
                                role="user",
                                content=human_message,
                                meta_data=human_meta,
                            )
                            await self._add_message_async(
                                message_id=message_id,
                                conversation_id=conversation_id_uuid,
                                role="assistant",
                                content=preset_response,
                                meta_data={"usage": {}, "audio_url": None, "moderation_flagged": True},
                            )
                        execution = await self._patch_execution_async(
                            execution.execution_id,
                            status="completed",
                            output_data=workflow_output_data,
                        )
                        # Synthesize LLM node_end events: emit only for LLM nodes
                        # that are currently executing
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
                        # Synthesize the workflow_end event and exit, no longer
                        # waiting for the LLM to finish silently
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
                            _log_run_stream_timing("moderation_finalize_done", status="completed")
                            yield end_event
                        break

                if event_type == "message":
                    if first_message_at is None:
                        first_message_at = time.perf_counter()
                        _log_run_stream_timing("first_message_chunk", content_len=len(event_data.get("content", "") or ""))
                    chunk = event_data.get("content", "") or ""
                    output_node_id = event_data.get("node_id")
                    if output_node_id:
                        ensure_output_order(output_node_id)
                        output_contents.setdefault(output_node_id, "")
                        output_contents[output_node_id] += chunk
                    if output_node_id:
                        accumulated_content = rebuild_multi_content()
                    else:
                        accumulated_content += chunk

                if event_type == "cycle_item":
                    cycle_id = event_data.get("cycle_id")
                    if cycle_id not in _cycle_items:
                        _cycle_items[cycle_id] = []
                    _cycle_items[cycle_id].append(event_data)

                if event.get("event") == "workflow_end":
                    workflow_end_seen_at = time.perf_counter()
                    _log_run_stream_timing(
                        "workflow_end_received",
                        status=event.get("data", {}).get("status"),
                    )
                    status = event.get("data", {}).get("status")
                    token_usage = event.get("data", {}).get("token_usage", {}) or {}
                    if status == "completed":
                        finalize_started_at = time.perf_counter()
                        canonical_outputs = event.get("data", {}).get("outputs")
                        if isinstance(canonical_outputs, list):
                            output_contents.clear()
                            output_order.clear()
                            for canonical_output in canonical_outputs:
                                if not isinstance(canonical_output, dict):
                                    continue
                                output_node_id = canonical_output.get("node_id")
                                content = canonical_output.get("content", "") or ""
                                if not output_node_id or not content:
                                    continue
                                ensure_output_order(str(output_node_id))
                                output_contents[str(output_node_id)] = content
                            accumulated_content = rebuild_multi_content()
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

                        canonical_final_content = (
                            assistant_message
                            if isinstance(assistant_message, str)
                            else accumulated_content
                        )
                        if accumulated_content != canonical_final_content:
                            logger.warning(
                                "Workflow final output invariant corrected: execution_id=%s "
                                "streamed_length=%s canonical_length=%s",
                                execution.execution_id,
                                len(accumulated_content),
                                len(canonical_final_content),
                            )
                            accumulated_content = canonical_final_content
                        if (
                                output_moderation
                                and not moderation_flagged
                                and output_moderation.replace_and_check(canonical_final_content)
                        ):
                            moderation_flagged = True
                            preset_response = output_moderation.preset_response
                            accumulated_content = preset_response
                            assistant_message = preset_response
                            output_contents.clear()
                            output_order.clear()
                            for message in reversed(final_messages):
                                if message.get("role") == "assistant":
                                    message["content"] = preset_response
                                    break
                            event.setdefault("data", {})["output"] = preset_response
                            event["data"]["moderation_flagged"] = True
                            event["data"]["preset_response"] = preset_response
                            yield {
                                "event": "message_replace",
                                "data": {
                                    "message_id": str(message_id),
                                    "node_id": None,
                                    "content": preset_response,
                                },
                            }
                        _user_msg = None
                        # Filter citations
                        citations = event.get("data", {}).get("citations", [])
                        citation_cfg = feature_configs.get("citation", {})
                        if isinstance(citation_cfg, dict) and citation_cfg.get("enabled"):
                            allow_download = citation_cfg.get("allow_download", False)
                            if allow_download:
                                from ..config import current_settings
                                for c in citations:
                                    if c.get("document_id"):
                                        c["download_url"] = f"{current_settings().file_local_server_url}/apps/citations/{c['document_id']}/download"
                            filtered_citations = citations
                        else:
                            filtered_citations = []
                        assistant_meta = {"usage": token_usage, "audio_url": None, "execution_id": execution.execution_id}
                        if filtered_citations:
                            assistant_meta["citations"] = filtered_citations
                        if output_contents:
                            assistant_meta["outputs"] = ordered_outputs()
                        # When output moderation triggers, write the moderation
                        # info into execution output_data
                        workflow_output_data = event.get("data") or {}
                        if output_contents:
                            workflow_output_data["outputs"] = ordered_outputs()
                        if output_moderation and output_moderation.is_flagged:
                            workflow_output_data["moderation_flagged"] = True
                            workflow_output_data["preset_response"] = output_moderation.preset_response
                        _token_usage_total = token_usage.get("total_tokens", None)
                        finalize_payload_ready_at = time.perf_counter()
                        logger.info(
                            "[TIMING] workflow.finalize_outer stage=payload_ready execution_id=%s "
                            "total_ms=%.2f step_ms=%.2f has_conversation=%s",
                            execution.execution_id,
                            (finalize_payload_ready_at - finalize_started_at) * 1000,
                            (finalize_payload_ready_at - finalize_started_at) * 1000,
                            bool(conversation_id_uuid),
                        )

                        if conversation_id_uuid:
                            _from_msg_id = await self._resolve_from_message_id_async(
                                payload.from_message_id,
                                conversation_id_uuid,
                            )
                            # Under regenerate_mode, regenerate_stream persists the
                            # versioned messages itself; save_messages must not be
                            # enqueued here, or the same message_id would hit a
                            # primary-key conflict; messages_finalized prevents
                            # double enqueueing when workflow_end is processed again.
                            if not skip_save and not regenerate_mode and not messages_finalized:
                                messages_finalized = True
                                execution = await self._finalize_completed_execution_async(
                                    execution_id=execution.execution_id,
                                    conversation_id=conversation_id_uuid,
                                    human_message=human_message,
                                    human_meta=human_meta,
                                    user_message_id=user_message_id,
                                    from_message_id=_from_msg_id,
                                    assistant_message_id=message_id,
                                    assistant_message=assistant_message,
                                    assistant_meta=assistant_meta,
                                    workflow_output_data=workflow_output_data,
                                    token_usage=_token_usage_total,
                                )
                            else:
                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    status="completed",
                                    output_data=workflow_output_data,
                                    token_usage=_token_usage_total,
                                )
                            finalize_context_ready_at = time.perf_counter()
                            logger.info(
                                "[TIMING] workflow.finalize_outer stage=context_ready execution_id=%s "
                                "total_ms=%.2f step_ms=%.2f",
                                execution.execution_id,
                                (finalize_context_ready_at - finalize_started_at) * 1000,
                                (finalize_context_ready_at - finalize_payload_ready_at) * 1000,
                            )
                        else:
                            execution = await self._patch_execution_async(
                                execution.execution_id,
                                status="completed",
                                output_data=workflow_output_data,
                                token_usage=_token_usage_total,
                            )
                        finalize_db_done_at = time.perf_counter()
                        logger.info(
                            "[TIMING] workflow.finalize_outer stage=db_done execution_id=%s "
                            "total_ms=%.2f step_ms=%.2f",
                            execution.execution_id,
                            (finalize_db_done_at - finalize_started_at) * 1000,
                            (finalize_db_done_at - (finalize_context_ready_at if conversation_id_uuid else finalize_payload_ready_at)) * 1000,
                        )
                        _log_run_stream_timing(
                            "workflow_end_finalize_done",
                            status="completed",
                            message_count=len(final_messages),
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
                        _failed_output = event.get("data")
                        _error_node_id = (_failed_output or {}).get("error_node")

                        if not skip_save and not regenerate_mode and not messages_finalized:
                            messages_finalized = True
                            await self._save_failed_conversation_async(
                                conversation_id_uuid, message_id, human_message, human_meta, error_msg
                            )
                        execution = await self._patch_execution_async(
                            execution.execution_id,
                            status="failed",
                            output_data=_failed_output,
                            error_node_id=_error_node_id,
                        )
                        _log_run_stream_timing("workflow_end_finalize_done", status="failed")
                    elif status == "waiting_human":
                        from .intervention_registry import register_intervention, register_pending

                        event_data = event.get("data", {})
                        interventions_raw = event_data.get("interventions", [])

                        # Persist node_outputs from the graph state so pre-intervention
                        # nodes are visible in the execution log.
                        graph_node_outputs = event_data.get("node_outputs") or {}
                        if graph_node_outputs:
                            import copy as _copy
                            new_output_data = _copy.deepcopy(execution.output_data or {})
                            merged_node_outputs = new_output_data.setdefault("node_outputs", {})
                            for nid, nout in graph_node_outputs.items():
                                merged_node_outputs[nid] = nout
                            execution.output_data = new_output_data

                        # Compute max execution_order so intervention nodes get correct ordering
                        max_exec_order = 0
                        if execution.output_data:
                            for nout in (execution.output_data.get("node_outputs") or {}).values():
                                if isinstance(nout, dict):
                                    max_exec_order = max(max_exec_order, nout.get("execution_order", 0))

                        intervention_list = []
                        interrupt_map = {}
                        registry_node_ids = set()  # nodes whose interrupt was lost (from registry)
                        for intr_raw in interventions_raw:
                            if not isinstance(intr_raw, dict):
                                continue
                            intr_info = {
                                "node_id": intr_raw.get("node_id", ""),
                                "node_name": intr_raw.get("node_name", ""),
                                "rendered_content": intr_raw.get("rendered_content", ""),
                                "form_fields": intr_raw.get("form_fields", []),
                                "actions": intr_raw.get("actions", []),
                                "timeout_at": intr_raw.get("timeout_at"),
                            }
                            is_from_registry = intr_raw.get("__from_registry__", False)
                            iid = intr_raw.get("__interrupt_id__", "")
                            if iid and not is_from_registry:
                                intr_info["interrupt_id"] = iid
                                interrupt_map[intr_info["node_id"]] = iid
                            else:
                                # Synthetic interrupt — node was cancelled by LangGraph
                                # before it could save its interrupt to the checkpointer.
                                # Treat it as a "pending" node so resume uses Command(goto=...).
                                registry_node_ids.add(intr_info["node_id"])
                            intervention_list.append(intr_info)

                            # Always write intervention node details; deepcopy tolerates None via `or {}`
                            import copy as _copy
                            new_output_data = _copy.deepcopy(execution.output_data or {})
                            node_outputs = new_output_data.setdefault("node_outputs", {})
                            node_outputs[intr_info["node_id"]] = {
                                "node_type": "human-intervention",
                                "node_name": intr_raw.get("node_name", ""),
                                "status": "waiting",
                                "input": None,
                                "output": {
                                    "rendered_content": intr_info["rendered_content"],
                                    "actions": intr_info["actions"],
                                    "delivery_method": intr_raw.get("delivery_method", {
                                        "webapp": {"enabled": True},
                                        "email": {"enabled": False},
                                    }),
                                },
                                "elapsed_time": None,
                                "token_usage": None,
                                "error": None,
                                "execution_order": max_exec_order + 1,
                            }
                            execution.output_data = new_output_data

                        if intervention_list:
                            first_node_id = intervention_list[0].get("node_id", "")
                            timeout_at = self._activate_visible_intervention_timeout(
                                execution.execution_id, first_node_id, config
                            )
                            if timeout_at is not None:
                                intervention_list = self._replace_intervention_timeout_at(
                                    intervention_list, first_node_id, timeout_at
                                )

                        _hitl_context = {
                            **(execution.context or {}),
                            "human_intervention": {
                                "message_id": str(message_id),
                                "conversation_id": str(conversation_id_uuid),
                                "checkpoint_thread_id": event_data.get("checkpoint_thread_id", ""),
                                "interventions": self._first_visible_intervention(intervention_list),
                                "intervention_backlog": intervention_list,
                            },
                        }
                        # Fix for an empty node_executions_map: earlier code merges
                        # node_outputs into execution.output_data (ORM memory), but
                        # the core query.update originally refreshed only
                        # status+context and dropped the dirty output_data attribute,
                        # so node details were not persisted on waiting_human and
                        # switch-version/branch reads saw an empty
                        # node_executions_map. Include output_data in the update columns.

                        human_message = payload.message or ""
                        human_meta = {"files": []}
                        for f in files or []:
                            if isinstance(f, dict):
                                human_meta["files"].append({
                                    "type": f.get("type"),
                                    "url": f.get("url"),
                                    "file_type": f.get("file_type"),
                                    "name": f.get("name"),
                                    "size": f.get("size"),
                                })

                        execution = await self._patch_execution_async(
                            execution.execution_id,
                            status="waiting_human",
                            context=_hitl_context,
                            output_data=execution.output_data,
                        )
                        _log_run_stream_timing(
                            "workflow_end_finalize_done",
                            status="waiting_human",
                            interventions=len(intervention_list),
                        )

                        _from_msg_id = await self._resolve_from_message_id_async(
                            payload.from_message_id,
                            conversation_id_uuid,
                        )
                        # _add_message_async returns the ORM entity after committing
                        # in a separate short-lived Session. Only pass its stable
                        # UUID; do not access its attributes here, to avoid
                        # detached-instance errors.
                        hitl_user_message_id = user_message_id or uuid.uuid4()
                        # Under regenerate_mode the placeholder messages are
                        # likewise persisted by regenerate_stream at the end
                        if conversation_id_uuid and not skip_save and not regenerate_mode:
                            await self._add_message_async(
                                conversation_id=conversation_id_uuid,
                                role="user",
                                content=human_message,
                                meta_data=human_meta,
                                parent_message_id=_from_msg_id,
                                message_id=hitl_user_message_id,
                            )
                            await self._add_message_async(
                                message_id=message_id,
                                conversation_id=conversation_id_uuid,
                                role="assistant",
                                content="",
                                meta_data={"waiting_human": True, "execution_id": execution.execution_id},
                                parent_message_id=hitl_user_message_id,
                            )

                        # message_id is already saved in execution.context["human_intervention"]["message_id"]
                        # at line ~1653, so resume_intervention_stream can find it there.

                        logger.info(
                            f"Workflow paused for human intervention: "
                            f"execution_id={execution.execution_id}, "
                            f"interrupt_nodes={list(interrupt_map.keys())}, "
                            f"registry_nodes={list(registry_node_ids)}"
                        )

                        if intervention_list:
                            register_intervention(execution.execution_id, interrupt_map)
                            if registry_node_ids:
                                register_pending(execution.execution_id, registry_node_ids)

                            for intr_info in intervention_list:
                                queued_intervention_events.append({
                                    "event": "intervention_required",
                                    "data": {
                                        "execution_id": execution.execution_id,
                                        "node_id": intr_info["node_id"],
                                        "node_name": intr_info.get("node_name", ""),
                                        "rendered_content": intr_info["rendered_content"],
                                        "form_fields": intr_info.get("form_fields", []),
                                        "actions": intr_info["actions"],
                                        "timeout_at": self._iso_to_ms(intr_info.get("timeout_at")),
                                    }
                                })
                            # Emit only the first intervention; the rest will be
                            # emitted one at a time after each cycle completes.
                            if queued_intervention_events:
                                yield queued_intervention_events.pop(0)
                            break
                    else:
                        logger.error(f"unexpect workflow run status, status: {status}")
                    # Write the accumulated cycle_items into
                    # workflow_executions.output_data["node_outputs"]
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
                        execution = await self._patch_execution_async(
                            execution.execution_id,
                            output_data=execution.output_data,
                        )

                    # Plain streaming execution previously updated only
                    # workflow_executions and missed the node projection. Enqueue
                    # it after cycle_items are merged and before workflow_end is
                    # returned to the client, so both trial runs and share
                    # experience can write workflow_node_executions.
                    if status in ("completed", "failed"):
                        try:
                            await self._persist_execution_artifacts_async(
                                execution.execution_id,
                                config,
                            )
                        except Exception as persist_err:
                            logger.warning(
                                "Failed to persist node executions on workflow end: %s",
                                persist_err,
                                exc_info=True,
                            )
                elif event.get("event") == "workflow_start":
                    event["data"]["message_id"] = str(message_id)
                    if user_message_id:
                        event["data"]["user_message_id"] = str(user_message_id)
                # Track active LLM nodes: added on node_start, removed on
                # node_end; synthesis re-emits only for active nodes
                if event_type == "node_start" and event_data.get("node_id") in llm_node_ids:
                    active_llm_nodes.add(event_data.get("node_id"))
                if event_type == "node_end" and event_data.get("node_id") in llm_node_ids:
                    active_llm_nodes.discard(event_data.get("node_id"))
                event = self._emit(public, event)
                if event:
                    yield event

            if intervention_list:
                from .intervention_registry import wait_for_next, register_intervention, cleanup_intervention, register_pending

                all_intervention_node_ids = {info.get("node_id") for info in intervention_list if info.get("node_id")}
                current_pending_node_ids = set(registry_node_ids)

                # Track resolved nodes across ALL resume cycles.
                # Any node the user has clicked is "resolved" and must NEVER
                # appear again in intervention_required events, regardless of
                # what the graph state or registry says.
                resolved_node_ids: set[str] = set()

                while True:
                    node_id, action_id, form_data, interrupt_id, kind = await wait_for_next(execution.execution_id)
                    if not node_id:
                        break

                    # Archive the just-resolved node into resolved_interventions
                    # (append-only) so the API can return its original form
                    # content + the user's submitted action/form_data later,
                    # even after a later intervention node overwrites the
                    # pending `interventions` list.
                    intx_ctx = (execution.context or {}).get("human_intervention", {})
                    _original = next(
                        (
                            i for i in [
                                *(intx_ctx.get("interventions") or []),
                                *(intx_ctx.get("intervention_backlog") or []),
                            ]
                            if i.get("node_id") == node_id
                        ),
                        None,
                    )
                    if _original is not None:
                        _archived = {
                            **_original,
                            "resolved_action_id": action_id,
                            "resolved_form_data": form_data,
                            "resolved_at": to_iso_z(utcnow_naive()),
                            "resolved_kind": kind,
                        }
                        _new_ctx = {
                            **(execution.context or {}),
                            "human_intervention": {
                                **intx_ctx,
                                "resolved_interventions": [
                                    *(intx_ctx.get("resolved_interventions") or []),
                                    _archived,
                                ],
                            },
                        }
                        # Fix: execution became detached after close, so reopen an
                        # independent Session through the async helper to persist
                        # context, ensuring resolved_interventions reach the DB.
                        execution = await self._patch_execution_async(
                            execution.execution_id,
                            context=_new_ctx,
                        )

                    # Handle timeout signal from the background scheduler
                    # Resume the workflow via the timeout branch (TIMEOUT) instead of terminating
                    if kind == "timeout":
                        logger.info(
                            f"[RUN] received timeout signal, resuming via timeout branch "
                            f"for execution={execution.execution_id}, node={node_id}"
                        )
                        # Clean up timeout tracking — InterventionRegistry.remove_timeout
                        # was already called by the scheduler, but ensure cleanup here too
                        from ..workflow.nodes.human_intervention.node import InterventionRegistry as _IR
                        _IR.remove_timeout(execution.execution_id, node_id)
                        timeout_event = self._emit(
                            public,
                            self._build_intervention_timeout_event(
                                execution.execution_id, node_id, intx_ctx
                            ),
                        )
                        if timeout_event:
                            yield timeout_event

                    # Mark this node as resolved BEFORE resuming
                    resolved_node_ids.add(node_id)

                    more_pending = False
                    pending_intervention_events = []
                    captured_formatted_output = None

                    resume_kind = kind if kind in ("interrupt", "pending") else "interrupt"
                    if kind == "timeout" and not interrupt_id:
                        resume_kind = "pending"

                    async for resume_event in self.resume_workflow_stream(
                        execution_id=execution.execution_id,
                        app_id=app_id,
                        interrupt_id=interrupt_id,
                        action_id=action_id,
                        form_data=form_data,
                        filter_node_ids=all_intervention_node_ids,
                        resumed_node_id=node_id,
                        kind=resume_kind,
                        pending_node_ids=current_pending_node_ids,
                    ):
                        if resume_event.get("event") == "workflow_end" and resume_event.get("data", {}).get("status") == "waiting_human":
                            more_pending = True
                            remaining = resume_event.get("data", {}).get("interventions", [])
                            # Capture formatted output for potential use in Path 2
                            captured_formatted_output = resume_event.get("data", {}).get("formatted_output")
                            new_map = {}
                            new_pending = set()
                            clean_remaining = []
                            for intr in remaining:
                                iid = intr.get("__interrupt_id__", "")
                                nid = intr.get("node_id", "")

                                if nid in resolved_node_ids:
                                    logger.info(
                                        f"[RUN] filtering resolved node {nid} from interventions, "
                                        f"execution_id={execution.execution_id}"
                                    )
                                    continue

                                if iid:
                                    new_map[nid] = iid
                                clean = {
                                    "node_id": nid,
                                    "node_name": intr.get("node_name", nid),
                                    "rendered_content": intr.get("rendered_content", ""),
                                    "form_fields": intr.get("form_fields", []),
                                    "actions": intr.get("actions", []),
                                    "timeout_at": intr.get("timeout_at"),
                                }
                                if iid:
                                    clean["interrupt_id"] = iid
                                clean_remaining.append(clean)
                                all_intervention_node_ids.add(nid)
                                if not iid:
                                    new_pending.add(nid)

                            if clean_remaining:
                                # Persist the new pending interventions back to
                                # execution.context. Without this, the resume path
                                # only emits via SSE and never updates DB, so on
                                # reconnect the API would only see the original
                                # interventions list (and miss any new node that
                                # was triggered by the resume).
                                self._set_human_intervention_state(
                                    execution,
                                    visible_interventions=[],
                                    backlog_interventions=clean_remaining,
                                )
                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    context=execution.context,
                                )

                                register_intervention(execution.execution_id, new_map)
                                if new_pending:
                                    register_pending(execution.execution_id, new_pending)
                                    current_pending_node_ids.update(new_pending)
                                for intr_info in clean_remaining:
                                    pending_intervention_events.append({
                                        "event": "intervention_required",
                                        "data": {
                                            "execution_id": execution.execution_id,
                                            "node_id": intr_info["node_id"],
                                            "node_name": intr_info.get("node_name", ""),
                                            "rendered_content": intr_info["rendered_content"],
                                            "form_fields": intr_info["form_fields"],
                                            "actions": intr_info["actions"],
                                            "timeout_at": self._iso_to_ms(intr_info.get("timeout_at")),
                                        }
                                    })
                            else:
                                more_pending = False
                                # All remaining interventions were already resolved
                                # by the user. resume_workflow_stream yielded a
                                # workflow_end with status="waiting_human" (because
                                # stale interrupts lingered in graph state), which
                                # was intercepted by the if-branch above and NOT
                                # yielded to the client. We must synthesize a
                                # proper "completed" workflow_end here so the
                                # frontend knows the workflow has finished.
                                # Fix: execution detached from the session at the
                                # start of run_stream, so later in-memory mutations
                                # are not persisted automatically, leaving
                                # status/output_data at waiting_human/stale values
                                # after resume completes. Persist explicitly via
                                # query.update instead.
                                # Save the formatted output (built by
                                # resume_workflow_stream via build_final_output)
                                # as output_data so the log view displays the
                                # correct End node outputs, not a raw state dump.
                                # Use accumulated_content (from message events) as the
                                # final output to avoid duplicating content across cycles.
                                _final_output_data = None
                                _final_token_usage = None
                                if captured_formatted_output:
                                    if not accumulated_content and captured_formatted_output.get("output"):
                                        accumulated_content = captured_formatted_output["output"]
                                    captured_formatted_output["output"] = accumulated_content
                                    _final_output_data = captured_formatted_output
                                    if captured_formatted_output.get("token_usage"):
                                        _final_token_usage = captured_formatted_output["token_usage"]

                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    status="completed",
                                    output_data=_final_output_data,
                                    token_usage=_final_token_usage,
                                )

                                # Always clear waiting_human on the original
                                # message, even when accumulated_content is empty
                                # (e.g. workflow has no LLM after the interventions).
                                # Otherwise the message keeps the
                                # `waiting_human: true` flag forever and the
                                # frontend can't tell the intervention is done.
                                _resolved_meta = {
                                    "waiting_human": False,
                                    "execution_id": execution.execution_id,
                                }
                                if accumulated_content:
                                    _assistant_meta = {"usage": execution.token_usage or {}}
                                    if captured_formatted_output and captured_formatted_output.get("citations"):
                                        _assistant_meta["citations"] = captured_formatted_output["citations"]
                                    _resolved_meta.update(_assistant_meta)
                                    await self._update_message_async(
                                        message_id=message_id,
                                        content=accumulated_content,
                                        meta_data=_resolved_meta,
                                    )
                                else:
                                    await self._update_message_async(
                                        message_id=message_id,
                                        meta_data=_resolved_meta,
                                    )

                                try:
                                    await self._persist_execution_artifacts_async(execution.execution_id, config)
                                except Exception as persist_err:
                                    logger.warning(f"Failed to persist node executions on intervention resolved: {persist_err}")

                                from ..workflow.nodes.human_intervention.node import InterventionRegistry as _IR
                                _IR.cleanup(execution.execution_id)

                                # Clean up checkpointer from cache to prevent memory leak
                                intervention_ctx = (execution.context or {}).get("human_intervention", {})
                                cp_thread_id = intervention_ctx.get("checkpoint_thread_id", "")
                                if cp_thread_id:
                                    from ..workflow.engine.graph_builder import remove_checkpointer
                                    remove_checkpointer(cp_thread_id)

                                yield self._emit(public, {
                                    "event": "workflow_end",
                                    "data": {
                                        "status": "completed",
                                        "execution_id": execution.execution_id,
                                        "elapsed_time": execution.elapsed_time,
                                        "output": accumulated_content,
                                    }
                                })
                                logger.info(
                                    f"[RUN] all remaining interventions filtered out, workflow complete, "
                                    f"resolved={resolved_node_ids}, execution_id={execution.execution_id}"
                                )
                                _log_run_stream_timing("resume_filtered_finalize_done", status="completed")
                        else:
                            # Accumulate message event content for the final output.
                            # Only message events carry the actual streaming text;
                            # captured_formatted_output.output / resume_data.output
                            # overlap with message content and must NOT be added here
                            # to avoid duplication across resume cycles.
                            if resume_event.get("event") == "message":
                                accumulated_content += resume_event.get("data", {}).get("content", "") or ""

                            if resume_event.get("event") == "workflow_end" and resume_event.get("data", {}).get("status") == "completed":
                                # Persist full accumulated output to DB
                                resume_data = resume_event.get("data", {})

                                # Fallback: if no message events were received during
                                # resume, use the output from formatted_output built
                                # by resume_workflow_stream (which extracts End node output
                                # from graph state).
                                if not accumulated_content and resume_data.get("output"):
                                    accumulated_content = resume_data["output"]

                                resume_data["output"] = accumulated_content
                                # Same fix as the earlier block: after execution
                                # detaches, ORM assignment is not persisted; use
                                # the core query.update instead
                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    status="completed",
                                    output_data=resume_data,
                                    token_usage=resume_data.get("token_usage"),
                                )

                                # Always clear waiting_human on the original
                                # message, even when accumulated_content is empty
                                # (e.g. workflow has no LLM after the interventions).
                                # Otherwise the message keeps the
                                # `waiting_human: true` flag forever and the
                                # frontend can't tell the intervention is done.
                                _resolved_meta = {
                                    "waiting_human": False,
                                    "execution_id": execution.execution_id,
                                }
                                if accumulated_content:
                                    _assistant_meta = {"usage": resume_data.get("token_usage", {}) or {}}
                                    if resume_data.get("citations"):
                                        _assistant_meta["citations"] = resume_data["citations"]
                                    _resolved_meta.update(_assistant_meta)
                                    await self._update_message_async(
                                        message_id=message_id,
                                        content=accumulated_content,
                                        meta_data=_resolved_meta,
                                    )
                                else:
                                    await self._update_message_async(
                                        message_id=message_id,
                                        meta_data=_resolved_meta,
                                    )

                                try:
                                    await self._persist_execution_artifacts_async(execution.execution_id, config)
                                except Exception as persist_err:
                                    logger.warning(f"Failed to persist node executions on intervention complete: {persist_err}")

                                logger.info(
                                    f"[RUN] workflow completed via intervention loop, "
                                    f"accumulated_output_length={len(accumulated_content)}, "
                                    f"execution_id={execution.execution_id}"
                                )

                            # If a workflow_end with status failed is encountered
                            # during resume
                            if resume_event.get("event") == "workflow_end" and resume_event.get("data", {}).get("status") == "failed":
                                resume_data = resume_event.get("data", {})
                                # If resume_data contains node_outputs (the graph
                                # returned a failed status normally, e.g. _wrap_error
                                # with an error edge), update execution.output_data
                                # with it. Otherwise (exception path:
                                # resume_workflow_stream yields only {status, error,
                                # execution_id}), keep the existing
                                # execution.output_data (exception handling already
                                # built data including node_outputs) and do not overwrite.
                                if isinstance(resume_data.get("node_outputs"), dict) and resume_data["node_outputs"]:
                                    execution.output_data = resume_data
                                elif not execution.output_data:
                                    execution.output_data = resume_data
                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    status="failed",
                                    output_data=execution.output_data,
                                    error_node_id=resume_data.get("error_node"),
                                    error_message=resume_data.get("error"),
                                )

                                # On failure, clear the waiting_human flag and
                                # update the message
                                _resolved_meta = {"waiting_human": False, "execution_id": execution.execution_id}
                                await self._update_message_async(
                                    message_id=message_id,
                                    meta_data=_resolved_meta,
                                )

                                try:
                                    await self._persist_execution_artifacts_async(execution.execution_id, config)
                                except Exception as persist_err:
                                    logger.warning(f"Failed to persist node executions on intervention failed: {persist_err}")
                            emitted = self._emit(public, resume_event)
                            if emitted:
                                if resume_event.get("event") == "workflow_end" and resume_event.get("data", {}).get("status") == "completed":
                                    emitted["data"]["output"] = accumulated_content
                                yield emitted

                    if not more_pending:
                        # The resumed branch ended the workflow (completed,
                        # failed, timeout, etc.). Do not emit any previously
                        # queued parallel intervention events after a terminal
                        # workflow_end event.
                        queued_intervention_events.clear()
                        cleanup_intervention(execution.execution_id)
                        break

                    # Merge newly discovered interventions into the queue,
                    # deduplicating by node_id to avoid showing the same
                    # intervention twice.
                    existing_ids = {e["data"]["node_id"] for e in queued_intervention_events}
                    for evt in pending_intervention_events:
                        nid = evt["data"]["node_id"]
                        if nid not in existing_ids:
                            queued_intervention_events.append(evt)
                            existing_ids.add(nid)

                    # Emit the next queued intervention event (one at a time),
                    # skipping nodes that have already been resolved.
                    while queued_intervention_events:
                        next_evt = queued_intervention_events.pop(0)
                        next_node_id = next_evt["data"]["node_id"]
                        if next_node_id not in resolved_node_ids:
                            next_evt, timeout_at = self._activate_intervention_event_timeout(
                                execution.execution_id, next_evt, config
                            )
                            _cur_ctx = (execution.context or {}).get("human_intervention", {})
                            _backlog = _cur_ctx.get("intervention_backlog") or []
                            commit_context = False
                            if timeout_at is not None:
                                _backlog = self._replace_intervention_timeout_at(
                                    _backlog, next_node_id, timeout_at
                                )
                                self._set_human_intervention_state(
                                    execution,
                                    backlog_interventions=_backlog,
                                )
                                commit_context = True
                            _visible = self._interventions_by_node_ids(_backlog, {next_node_id})
                            if _visible:
                                self._set_human_intervention_state(
                                    execution,
                                    visible_interventions=_visible,
                                )
                                commit_context = True
                            if commit_context:
                                execution = await self._patch_execution_async(
                                    execution.execution_id,
                                    context=execution.context,
                                )
                            yield next_evt
                            break

        except Exception as e:
            logger.error(
                f"Workflow streaming execution failed: execution_id={execution.execution_id}, error={e}",
                exc_info=True
            )
            execution = await self._patch_execution_async(
                execution.execution_id,
                status="failed",
                error_message=str(e),
            )
            try:
                execution = await self._get_execution_async(execution.execution_id)
                if execution and execution.output_data:
                    await self._persist_workflow_node_executions_async(execution, config, execution.output_data)
                    await self._refresh_workflow_debug_state_from_execution_async(execution, workflow_config=config)
            except Exception as persist_err:
                logger.warning(f"Failed to persist node executions on stream error: {persist_err}")
            # Clean up registry on failure to avoid leaks.
            from ..workflow.nodes.human_intervention.node import InterventionRegistry
            InterventionRegistry.cleanup(execution.execution_id)
            human_message, human_meta = self._extract_human_message_and_meta([], payload.message or "", files)
            # If this round's messages were already enqueued
            # (messages_finalized), do not enqueue failure messages again, or
            # the same message_id would hit a messages primary-key conflict
            if not skip_save and not regenerate_mode and not messages_finalized:
                await self._save_failed_conversation_async(
                    conversation_id_uuid, None, human_message, human_meta, str(e)
                )
            # On failure, update the waiting_human message in-place:
            # clear the flag and set content to the error message.
            if conversation_id_uuid and message_id and not skip_save:
                await self._update_message_async(
                    message_id=message_id,
                    content=str(e),
                    meta_data={
                        "waiting_human": False,
                        "execution_id": execution.execution_id,
                        "error": str(e),
                    },
                )
            yield {
                "event": "workflow_end",
                "data": {
                    "status": "failed",
                    "execution_id": execution.execution_id,
                    "error": str(e),
                },
            }

        finally:
            # Stop the underlying workflow generator explicitly when this stream
            # exits early (moderation, disconnect, cancellation). This propagates
            # cancellation to inflight nodes so their provisional transactions can
            # run rollback cleanup.
            if workflow_event_stream is not None:
                try:
                    await asyncio.shield(workflow_event_stream.aclose())
                except (Exception, asyncio.CancelledError) as close_err:
                    logger.debug(
                        "Failed to close workflow event stream cleanly: execution_id=%s error=%s",
                        execution.execution_id if execution else None,
                        close_err,
                    )

            # A closed transport cannot receive message_replace. Restore all
            # remaining transactions internally so cancellation persistence and
            # moderation finalization never retain provisional text.
            if optimistic_checkpoints:
                restored_global = False
                for checkpoint in list(optimistic_checkpoints.values()):
                    output_node_id = checkpoint.get("output_node_id")
                    if checkpoint.get("scoped") and output_node_id:
                        if checkpoint.get("existed"):
                            output_contents[output_node_id] = checkpoint.get("content", "") or ""
                        else:
                            output_contents.pop(output_node_id, None)
                    else:
                        accumulated_content = checkpoint.get("content", "") or ""
                        output_contents.clear()
                        output_contents.update(checkpoint.get("outputs", {}))
                        restored_global = True
                optimistic_checkpoints.clear()
                if not restored_global:
                    accumulated_content = "\n".join(
                        output_contents[node_id]
                        for node_id in output_order
                        if output_contents.get(node_id)
                    )
                if output_moderation:
                    output_moderation.reset(accumulated_content)

            # ── On cancel/abort: persist variables + release lock + clean up resources ──
            if execution and execution.status == "running":
                try:
                    # Persist output_data only when the snapshot contains
                    # complete variables; incomplete snapshots (e.g. LLM
                    # streaming chunks) are not saved, to avoid overwriting the
                    # last known complete terminal state.
                    snapshot = (
                        _last_event_data
                        if self._is_snapshot_complete(_last_event_data)
                        else None
                    )
                    await self._patch_execution_async(
                        execution.execution_id,
                        status="cancelled",
                        output_data=snapshot,
                    )
                    if snapshot is None:
                        logger.warning(
                            "cancelled without complete variable snapshot: "
                            "execution_id=%s conversation_id=%s",
                            execution.execution_id, conversation_id_uuid,
                        )
                except Exception as persist_err:
                    logger.warning(
                        "Failed to persist variables on cancel: execution_id=%s error=%s",
                        execution.execution_id, persist_err,
                    )

            # Release the conversation execution lock
            if _lock_key and _lock_value and not _lock_degraded:
                try:
                    _release_script = """
                    if redis.call("get", KEYS[1]) == ARGV[1] then
                        return redis.call("del", KEYS[1])
                    end
                    return 0
                    """
                    await aio_redis.execute_lua(_release_script, 1, _lock_key, _lock_value)
                except Exception as lock_release_err:
                    logger.warning(
                        "Failed to release execution lock: key=%s error=%s",
                        _lock_key, lock_release_err,
                    )

            # Clean up the intervention registry
            try:
                from ..workflow.nodes.human_intervention.node import InterventionRegistry
                InterventionRegistry.cleanup(execution.execution_id)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to cleanup intervention registry: execution_id=%s error=%s",
                    execution.execution_id, cleanup_err,
                )

    @staticmethod
    def _is_snapshot_complete(data: dict | None) -> bool:
        """Whether SSE event data contains a complete variable snapshot
        (including variables.conv).

        Only workflow_end events carry the full variables field; intermediate
        events such as message/node_end do not, and must not be used as a
        variable recovery source for cancelled runs.
        """
        if not isinstance(data, dict):
            return False
        if "variables" not in data:
            return False
        variables = data.get("variables") or {}
        return isinstance(variables, dict) and "conv" in variables

    async def resume_intervention_stream(
            self,
            execution_id: str,
            app_id: uuid.UUID,
            node_id: str,
            action_id: str,
            form_data: dict[str, Any] | None = None,
            public: bool = False,
    ):
        """Resume a workflow that is waiting for human intervention.

        Used when the SSE stream was lost (page refresh) and the user
        submits their action via a new HTTP request. This method
        re-registers the intervention queue, pushes the user's action,
        and re-enters the intervention handling loop to produce SSE events.

        Args:
            execution_id: The execution_id of the waiting_human execution.
            app_id: The app ID (for workflow config lookup).
            node_id: The intervention node ID the user is responding to.
            action_id: The action the user chose.
            form_data: Optional form data from the user.
            public: Whether to emit public-facing events.

        Yields:
            SSE events (same format as run_stream).
        """
        from .intervention_registry import (
            register_intervention, wait_for_next, cleanup_intervention,
        )
        from ..workflow.nodes.human_intervention.node import InterventionRegistry as _IR

        if self.execution_repo is None:
            # Prefer the DB execution; fall back to the Redis runtime snapshot
            # when not persisted. Always return a WorkflowExecutionRef, avoiding
            # expunge on a transient ORM instance or accessing lazy relations
            # after a short-lived Session has closed.
            execution = await self._get_execution_async(execution_id)
        else:
            execution = self.get_execution(execution_id)
        if not execution:
            raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)
        if execution.status != "waiting_human":
            raise BusinessException(
                f"当前执行状态为 '{execution.status}'，不接受人工介入响应",
                BizCode.BAD_REQUEST,
            )

        intervention_ctx = (execution.context or {}).get("human_intervention", {})
        resolved_node_ids = self._resolved_intervention_node_ids(intervention_ctx)
        conversation_id_str = intervention_ctx.get("conversation_id", "")
        conversation_id_uuid = uuid.UUID(conversation_id_str) if conversation_id_str else None

        # Retrieve the message_id of the original waiting_human assistant
        # message saved in execution context by run_stream. This allows us
        # to update the same message instead of creating a new one.
        hitl_message_id_str = intervention_ctx.get("message_id", "")
        hitl_message_id = uuid.UUID(hitl_message_id_str) if hitl_message_id_str else None

        visible_interventions = intervention_ctx.get("interventions") or []
        saved_interventions = (
                intervention_ctx.get("intervention_backlog")
                or visible_interventions
                or []
        )
        submitted_intervention = self._find_intervention_by_node_id(
            [*visible_interventions, *saved_interventions],
            node_id,
        )
        is_expired_submit = self._intervention_is_expired(submitted_intervention)
        effective_action_id = "__timeout__" if is_expired_submit else action_id
        effective_form_data = None if is_expired_submit else form_data

        # Build interrupt_map from saved interventions (only those with interrupt_id)
        interrupt_map = {}
        pending_node_ids = set()
        all_intervention_node_ids = set(resolved_node_ids)
        for intr in saved_interventions:
            nid = intr.get("node_id", "")
            iid = intr.get("interrupt_id", "")
            if nid:
                all_intervention_node_ids.add(nid)
            if iid and nid:
                interrupt_map[nid] = iid
            elif nid:
                pending_node_ids.add(nid)

        # Re-register the intervention queue so wait_for_next can consume.
        # Must cleanup first to discard the old queue (the original SSE
        # stream's generator is stuck waiting on it), then create a fresh
        # queue that our new generator will consume.
        cleanup_intervention(execution_id)
        register_intervention(execution_id, interrupt_map)
        if pending_node_ids:
            from .intervention_registry import register_pending
            register_pending(execution_id, pending_node_ids)

        if is_expired_submit:
            logger.info(
                f"[RESUME-INT] intervention submit expired, routing timeout branch: "
                f"execution={execution_id}, node={node_id}"
            )
            from .intervention_registry import signal_timeout
            result = signal_timeout(execution_id, node_id)
            result_kind = "timeout" if result else None
        else:
            # Push the user's action into the queue immediately
            from .intervention_registry import submit_intervention
            result_kind = submit_intervention(
                execution_id, node_id, effective_action_id, effective_form_data
            )
        if not result_kind:
            raise BusinessException(
                "提交干预请求失败，请重试",
                BizCode.BAD_REQUEST,
            )

        # Record resolved action/form_data in the execution context so the
        # conversation endpoint can show the completed intervention even after
        # the workflow finishes (preserves history, not just pending state).
        execution = await self._get_execution_async(execution_id)
        if execution:
            # Append the just-resolved intervention to resolved_interventions
            # (append-only). Without this, when the resume loop later replaces
            # `interventions` with the next pending node, the resolved node's
            # data is lost — the API would then only see the new pending node
            # and not the history.
            # Determine the kind: the registry routes based on whether the
            # node is in pending_nodes ("pending") or node_map ("interrupt").
            from .intervention_registry import _state as _ir_state
            _entry = _ir_state.get(execution_id)
            _archived_kind = result_kind or "interrupt"
            if _entry is not None and _archived_kind != "timeout":
                if node_id in _entry.get("pending_nodes", set()):
                    _archived_kind = "pending"
                elif node_id in _entry.get("node_map", {}):
                    _archived_kind = "interrupt"
            execution = await self._archive_resolved_intervention_async(
                execution_id,
                node_id,
                effective_action_id,
                effective_form_data,
                _archived_kind,
            )
            intervention_ctx = (execution.context or {}).get("human_intervention", {})
            resolved_node_ids = self._resolved_intervention_node_ids(intervention_ctx)
            resolved_node_ids.add(node_id)
            all_intervention_node_ids.update(resolved_node_ids)

        if self.config_repo is None:
            config = await self.get_workflow_config_snapshot_async(app_id)
        else:
            config = self.get_workflow_config(app_id)
        if not config:
            raise BusinessException("工作流配置不存在", BizCode.CONFIG_MISSING)

        # Clean up the resumed node from InterventionRegistry
        _IR.cleanup_node(execution_id, node_id)

        current_pending_node_ids = set(pending_node_ids)
        accumulated_content = ""
        message_id = uuid.uuid4()

        while True:
            next_node_id, next_action_id, next_form_data, next_interrupt_id, next_kind = \
                await wait_for_next(execution_id)
            if not next_node_id:
                break

            if next_kind == "timeout":
                logger.info(
                    f"[RESUME-INT] timeout signal for execution={execution_id}, node={next_node_id}"
                )
                _IR.remove_timeout(execution_id, next_node_id)
                timeout_event = self._emit(
                    public,
                    self._build_intervention_timeout_event(
                        execution_id, next_node_id, intervention_ctx
                    ),
                )
                if timeout_event:
                    yield timeout_event

            execution = await self._get_execution_async(execution_id)
            if execution:
                execution = await self._archive_resolved_intervention_async(
                    execution_id, next_node_id, next_action_id, next_form_data, next_kind
                )
                intervention_ctx = (execution.context or {}).get("human_intervention", {})
                resolved_node_ids.update(self._resolved_intervention_node_ids(intervention_ctx))
                all_intervention_node_ids.update(resolved_node_ids)

            resolved_node_ids.add(next_node_id)

            more_pending = False
            pending_intervention_events = []
            captured_formatted_output = None

            resume_kind = next_kind if next_kind in ("interrupt", "pending") else "interrupt"
            if next_kind == "timeout" and not next_interrupt_id:
                resume_kind = "pending"

            async for resume_event in self.resume_workflow_stream(
                execution_id=execution_id,
                app_id=app_id,
                interrupt_id=next_interrupt_id,
                action_id=next_action_id,
                form_data=next_form_data,
                filter_node_ids=all_intervention_node_ids,
                resumed_node_id=next_node_id,
                kind=resume_kind,
                pending_node_ids=current_pending_node_ids,
            ):
                if resume_event.get("event") == "workflow_end" and \
                        resume_event.get("data", {}).get("status") == "waiting_human":
                    more_pending = True
                    remaining = resume_event.get("data", {}).get("interventions", [])
                    captured_formatted_output = resume_event.get("data", {}).get("formatted_output")
                    new_map = {}
                    new_pending = set()
                    clean_remaining = []
                    for intr in remaining:
                        iid = intr.get("__interrupt_id__", "")
                        nid = intr.get("node_id", "")
                        if nid in resolved_node_ids:
                            continue
                        if iid:
                            new_map[nid] = iid
                        clean = {
                            "node_id": nid,
                            "node_name": intr.get("node_name", nid),
                            "rendered_content": intr.get("rendered_content", ""),
                            "form_fields": intr.get("form_fields", []),
                            "actions": intr.get("actions", []),
                            "timeout_at": intr.get("timeout_at"),
                        }
                        if iid:
                            clean["interrupt_id"] = iid
                        else:
                            new_pending.add(nid)
                        clean_remaining.append(clean)
                        all_intervention_node_ids.add(nid)

                    if clean_remaining:
                        first_node_id = clean_remaining[0].get("node_id", "")
                        timeout_at = self._activate_visible_intervention_timeout(
                            execution_id, first_node_id, config
                        )
                        if timeout_at is not None:
                            clean_remaining = self._replace_intervention_timeout_at(
                                clean_remaining, first_node_id, timeout_at
                            )

                        register_intervention(execution_id, new_map)
                        if new_pending:
                            from .intervention_registry import register_pending
                            register_pending(execution_id, new_pending)
                            current_pending_node_ids.update(new_pending)
                        for intr_info in clean_remaining:
                            pending_intervention_events.append({
                                "event": "intervention_required",
                                "data": {
                                    "execution_id": execution_id,
                                    "node_id": intr_info["node_id"],
                                    "node_name": intr_info.get("node_name", ""),
                                    "rendered_content": intr_info["rendered_content"],
                                    "form_fields": intr_info["form_fields"],
                                    "actions": intr_info["actions"],
                                    "timeout_at": self._iso_to_ms(intr_info.get("timeout_at")),
                                }
                            })
                    else:
                        # All remaining resolved — synthesize completed end
                        more_pending = False
                        if captured_formatted_output:
                            if not accumulated_content and captured_formatted_output.get("output"):
                                accumulated_content = captured_formatted_output["output"]
                            captured_formatted_output["output"] = accumulated_content
                        execution = await self._patch_execution_async(
                            execution_id,
                            status="completed",
                            output_data=captured_formatted_output,
                            token_usage=(captured_formatted_output or {}).get("token_usage"),
                        )
                        await self._touch_conversation_async(conversation_id_uuid)

                        if conversation_id_uuid and accumulated_content:
                            assistant_meta = {"usage": execution.token_usage or {}}
                            if captured_formatted_output and captured_formatted_output.get("citations"):
                                assistant_meta["citations"] = captured_formatted_output["citations"]

                        # Update the original waiting_human message in-place
                        # instead of creating a new assistant message.
                        # Use hitl_message_id (saved in execution context) if
                        # available; otherwise fall back to query-based
                        # resolve_intervention_message.
                        # Always clear waiting_human, even when accumulated_content
                        # is empty (e.g. workflow has no LLM after the interventions).
                        if conversation_id_uuid:
                            resolved_meta = {"waiting_human": False, "execution_id": execution_id}
                            if accumulated_content:
                                resolved_meta.update(assistant_meta or {})
                            if hitl_message_id:
                                if accumulated_content:
                                    await self._update_message_async(
                                        hitl_message_id,
                                        content=accumulated_content,
                                        meta_data=resolved_meta,
                                    )
                                else:
                                    await self._update_message_async(
                                        hitl_message_id,
                                        meta_data=resolved_meta,
                                    )
                            elif accumulated_content:
                                logger.warning("hitl_message_id missing; skip in-place update for resolved intervention")

                        await self._touch_conversation_async(conversation_id_uuid)

                        try:
                            await self._persist_execution_artifacts_async(execution.execution_id, config)
                        except Exception as persist_err:
                            logger.warning(f"Failed to persist node executions on resume resolved: {persist_err}")

                        _IR.cleanup(execution_id)
                        intervention_ctx2 = (execution.context or {}).get("human_intervention", {})
                        cp_thread_id = intervention_ctx2.get("checkpoint_thread_id", "")
                        if cp_thread_id:
                            from ..workflow.engine.graph_builder import remove_checkpointer
                            remove_checkpointer(cp_thread_id)

                        yield self._emit(public, {
                            "event": "workflow_end",
                            "data": {
                                "status": "completed",
                                "execution_id": execution_id,
                                "elapsed_time": execution.elapsed_time,
                                "output": accumulated_content,
                            }
                        })

                    # Update DB context with remaining interventions
                    visible_interventions = self._first_visible_intervention(clean_remaining)
                    current_context = execution.context or {}
                    current_intervention_ctx = current_context.get("human_intervention", {})
                    _new_context = {
                        **current_context,
                        "human_intervention": {
                            **current_intervention_ctx,
                            "interventions": visible_interventions,
                            "intervention_backlog": clean_remaining if clean_remaining else [],
                        },
                    }
                    execution = await self._patch_execution_async(
                        execution_id,
                        context=_new_context,
                    )
                    await self._touch_conversation_async(
                        current_intervention_ctx.get("conversation_id") or conversation_id_uuid
                    )

                    for evt in pending_intervention_events[:1]:
                        yield evt
                else:
                    # Non-waiting_human events: yield immediately for real-time streaming
                    if resume_event.get("event") == "message":
                        accumulated_content += resume_event.get("data", {}).get("content", "") or ""

                    if resume_event.get("event") == "workflow_end" and \
                            resume_event.get("data", {}).get("status") == "completed":
                        resume_data = resume_event.get("data", {})
                        if not accumulated_content and resume_data.get("output"):
                            accumulated_content = resume_data["output"]
                        resume_data["output"] = accumulated_content
                        execution = await self._patch_execution_async(
                            execution_id,
                            status="completed",
                            output_data=resume_data,
                            token_usage=resume_data.get("token_usage"),
                        )
                        if conversation_id_uuid:
                            resolved_meta = {"waiting_human": False, "execution_id": execution_id}
                            if accumulated_content:
                                assistant_meta = {"usage": resume_data.get("token_usage", {}) or {}}
                                if resume_data.get("citations"):
                                    assistant_meta["citations"] = resume_data["citations"]
                                resolved_meta.update(assistant_meta)
                            if hitl_message_id:
                                if accumulated_content:
                                    await self._update_message_async(
                                        hitl_message_id,
                                        content=accumulated_content,
                                        meta_data=resolved_meta,
                                    )
                                else:
                                    await self._update_message_async(
                                        hitl_message_id,
                                        meta_data=resolved_meta,
                                    )
                            elif accumulated_content:
                                logger.warning("hitl_message_id missing; skip in-place update for completed resume")

                        await self._touch_conversation_async(conversation_id_uuid)

                        try:
                            await self._persist_execution_artifacts_async(execution.execution_id, config)
                        except Exception as persist_err:
                            logger.warning(f"Failed to persist node executions on resume complete: {persist_err}")

                    # If a workflow_end with status failed is encountered during
                    # resume
                    if resume_event.get("event") == "workflow_end" and \
                            resume_event.get("data", {}).get("status") == "failed":
                        resume_data = resume_event.get("data", {})
                        # If resume_data contains node_outputs (the graph returned
                        # a failed status normally, e.g. _wrap_error with an error
                        # edge), update execution.output_data with it. Otherwise
                        # (exception path: resume_workflow_stream yields only
                        # {status, error, execution_id}), keep the existing
                        # execution.output_data (exception handling already built
                        # data including node_outputs) and do not overwrite.
                        failed_output = execution.output_data
                        if isinstance(resume_data.get("node_outputs"), dict) and resume_data["node_outputs"]:
                            failed_output = resume_data
                        elif not failed_output:
                            failed_output = resume_data
                        execution = await self._patch_execution_async(
                            execution_id,
                            status="failed",
                            output_data=failed_output,
                            error_node_id=resume_data.get("error_node"),
                            error_message=resume_data.get("error"),
                        )

                        # On failure, clear the waiting_human flag and update the
                        # message
                        if conversation_id_uuid:
                            resolved_meta = {"waiting_human": False, "execution_id": execution_id}
                            if hitl_message_id:
                                await self._update_message_async(
                                    hitl_message_id,
                                    meta_data=resolved_meta,
                                )

                        await self._touch_conversation_async(conversation_id_uuid)

                        try:
                            await self._persist_execution_artifacts_async(execution.execution_id, config)
                        except Exception as persist_err:
                            logger.warning(f"Failed to persist node executions on resume failed: {persist_err}")
                    emitted = self._emit(public, resume_event)
                    if emitted:
                        if resume_event.get("event") == "workflow_end" and \
                                resume_event.get("data", {}).get("status") == "completed":
                            emitted["data"]["output"] = accumulated_content
                        yield emitted

            if not more_pending:
                cleanup_intervention(execution_id)
                break

    async def _build_node_context(
            self,
            app_id: uuid.UUID,
            node_id: str,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any],
            prefer_cached_runtime: bool = False,
    ):
        """Build the context needed for single-node execution
        (node_config, node, state, variable_pool)."""
        from ..workflow.engine.runtime_schema import ExecutionContext
        from ..workflow.engine.variable_pool import VariablePool, VariablePoolInitializer
        from ..workflow.engine.state_manager import WorkflowState
        from ..workflow.nodes.node_factory import NodeFactory

        if not config:
            if self.config_repo is None:
                config = await self.get_workflow_config_snapshot_async(app_id)
            else:
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

        hi_action_id = None
        hi_form_data = {}
        if node_config.get("type") == NodeType.HUMAN_INTERVENTION:
            inputs_dict = input_data.get("inputs") or {}
            if isinstance(inputs_dict, dict):
                hi_action_id = inputs_dict.pop("__action_id", None)
                hi_form_data = inputs_dict.pop("__form_data", {}) or {}
                if not isinstance(hi_form_data, dict):
                    hi_form_data = {}

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
        if prefer_cached_runtime:
            # Rerun should inherit the latest debug/cache state as the source of truth.
            await self._apply_input_data_overrides_to_variable_pool(
                variable_pool=variable_pool,
                input_data=input_data,
            )
            await self._apply_start_node_cache_to_variable_pool(
                variable_pool=variable_pool,
                runtime_snapshot=runtime_seed["runtime_snapshot"],
                workflow_config=config,
            )
        else:
            # Manual single-node run should honor the current request inputs first.
            await self._apply_start_node_cache_to_variable_pool(
                variable_pool=variable_pool,
                runtime_snapshot=runtime_seed["runtime_snapshot"],
                workflow_config=config,
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

        # Single-node trial run of a human-intervention node: inject the flag
        # and the user action data
        if node_config.get("type") == NodeType.HUMAN_INTERVENTION:
            state["__single_node_run"] = True
            if hi_action_id:
                state["__single_node_action_id"] = hi_action_id
                state["__single_node_form_data"] = hi_form_data

        node = NodeFactory.create_node(node_config, workflow_config_dict, [])
        return node_config, node, state, variable_pool

    async def run_single_node(
            self,
            app_id: uuid.UUID,
            node_id: str,
            config: WorkflowConfig,
            workspace_id: uuid.UUID,
            input_data: dict[str, Any] | None = None,
            prefer_cached_runtime: bool = False,
    ) -> dict[str, Any]:
        """Single-node execution (non-streaming)"""
        input_data = input_data or {}
        node_config, node, state, variable_pool = await self._build_node_context(
            app_id, node_id, config, workspace_id, input_data, prefer_cached_runtime
        )
        run_id = self._build_single_node_run_id()
        start_time = time.time()
        try:
            cached_state_update = await node._try_use_cache(state, variable_pool, start_time)
            if cached_state_update:
                node_output = (cached_state_update.get("node_outputs") or {}).get(node_id) or {}
                payload = self._build_single_node_payload_from_node_output(
                    node_id=node_id,
                    node_type=node_config.get("type"),
                    node_output=node_output,
                )
            else:
                result = await asyncio.wait_for(
                    node.execute(state, variable_pool),
                    timeout=node.get_timeout(),
                )
                elapsed = (time.time() - start_time) * 1000
                output = node._extract_output(result)

                process = node._extract_extra_fields(result).get("process")
                is_preview = isinstance(result, dict) and result.get("__preview")
                if isinstance(process, dict) and process.get("preview"):
                    is_preview = True
                payload = {
                    "status": "preview" if is_preview else "completed",
                    "node_id": node_id,
                    "node_type": node_config.get("type"),
                    "inputs": node._extract_input(state, variable_pool),
                    "outputs": output,
                    "process": process,
                    "token_usage": node._extract_token_usage(result),
                    "elapsed_time": elapsed,
                    "error": None,
                }
                await node._save_cache(
                    state=state,
                    variable_pool=variable_pool,
                    node_output=self._normalize_single_node_payload(
                        node_config.get("type"),
                        node_config.get("name"),
                        payload,
                    ),
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
            elapsed = (time.time() - start_time) * 1000
            error_message = (
                f"Node execution timed out ({node.get_timeout()} seconds)."
                if isinstance(e, TimeoutError)
                else str(e)
            )
            logger.error(f"单节点执行失败: node_id={node_id}, error={e}", exc_info=True)
            payload = {
                "status": "failed",
                "execution_id": run_id,
                "node_id": node_id,
                "node_type": node_config.get("type"),
                "inputs": node._extract_input(state, variable_pool),
                "outputs": None,
                "token_usage": None,
                "elapsed_time": elapsed,
                "error": error_message,
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
            prefer_cached_runtime: bool = False,
    ):
        """Single-node execution (streaming)

        Yields:
            node_start -> node_chunk (for streaming nodes such as LLM) -> node_end / node_error
        """
        input_data = input_data or {}
        node_config, node, state, variable_pool = await self._build_node_context(
            app_id, node_id, config, workspace_id, input_data, prefer_cached_runtime
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
            process = extra_fields.get("process")
            is_preview = isinstance(final_result, dict) and final_result.get("__preview")
            if isinstance(process, dict) and process.get("preview"):
                is_preview = True
            event_payload = {
                "event": "node_end",
                "data": self._attach_debug_execution_id(
                    {
                        "status": "preview" if is_preview else "completed",
                        "node_id": node_id,
                        "node_type": node_type,
                        "inputs": node._extract_input(state, variable_pool),
                        "outputs": node._extract_output(final_result),
                        "process": process,
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
            # An Agent node with the long-term memory tool enabled also counts
            # as memory enabled
            if node.get("type") == NodeType.AGENT and \
                    node.get("config", {}).get("long_term_memory", {}).get("enable", False):
                return True
        return False

    @staticmethod
    def _validate_file_upload(
            features_config: dict[str, Any],
            files: Optional[list[FileInput]]
    ) -> None:
        """Validate that uploaded files comply with the file_upload config."""
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

        # Validate the transfer method
        allowed_methods = fu.get("allowed_transfer_methods", ["local_file", "remote_url"])
        for f in files:
            if f.transfer_method.value not in allowed_methods:
                raise BusinessException(
                    f"Unsupport file transfer method：{f.transfer_method.value},"
                    f"allowed method:{', '.join(allowed_methods)}",
                    BizCode.BAD_REQUEST
                )

        # Config keys for the enabled flag and size limit of each type
        type_cfg = {
            "image": ("image_enabled", "image_max_size_mb", 20, "image"),
            "audio": ("audio_enabled", "audio_max_size_mb", 50, "audio"),
            "document": ("document_enabled", "document_max_size_mb", 100, "document"),
            "video": ("video_enabled", "video_max_size_mb", 500, "video"),
        }

        for f in files:
            ftype = str(f.type)  # e.g. "image", "audio", "document", "video"
            cfg = type_cfg.get(ftype)
            if cfg is None:
                continue
            enabled_key, size_key, default_max_mb, label = cfg

            # Validate the type's enabled flag
            if not fu.get(enabled_key):
                raise BusinessException(
                    f"The application has not enabled {label} file upload",
                    BizCode.BAD_REQUEST
                )

            # Validate file size (only when the content has been loaded)
            content = f.get_content()
            if content is not None:
                max_mb = fu.get(size_key, default_max_mb)
                size_mb = len(content) / (1024 * 1024)
                if size_mb > max_mb:
                    raise BusinessException(
                        f"{label} File size exceeds the limit (maximum {max_mb} MB, current {size_mb:.1f} MB)",
                        BizCode.BAD_REQUEST
                    )

    async def resume_workflow_stream(
            self,
            execution_id: str,
            app_id: uuid.UUID,
            interrupt_id: str | None = None,
            action_id: str | None = None,
            form_data: dict | None = None,
            filter_node_ids: set[str] | None = None,
            resumed_node_id: str | None = None,
            kind: str = "interrupt",
            pending_node_ids: set[str] | None = None,
    ):
        """Stream-resume a workflow execution paused for human intervention

        Args:
            execution_id: execution record ID
            app_id: app ID
            interrupt_id: interrupt ID to resume (interrupt mode)
            action_id: action ID chosen by the user
            filter_node_ids: set of human-intervention node IDs whose node_start events should be filtered
            resumed_node_id: node ID currently being resumed, used for updates filtering
            kind: "interrupt" = resume an interrupt, "pending" = re-execute a pending node
            pending_node_ids: set of intervention node IDs previously completed with a __pending__ placeholder (used to filter events)

        Yields:
            dict: SSE-format streaming events
        """
        from langgraph.types import Command
        from ..workflow.executor import WorkflowExecutor
        from ..workflow.engine.runtime_schema import ExecutionContext
        from ..workflow.engine.variable_pool import VariablePoolInitializer

        # The streaming entry runs with WorkflowService() (no sync db/repository);
        # the resume path must query via an independent async Session and must not
        # call get_execution/get_workflow_config.
        execution = await self._get_execution_async(execution_id)
        if not execution:
            raise BusinessException("执行记录不存在", BizCode.NOT_FOUND)

        intervention_ctx = (execution.context or {}).get("human_intervention", {})
        thread_id_str = intervention_ctx.get("checkpoint_thread_id")

        config = await self.get_workflow_config_snapshot_async(app_id)
        if not config:
            raise BusinessException("工作流配置不存在", BizCode.CONFIG_MISSING)

        async with get_async_db_context() as db:
            workspace_id = await db.scalar(
                select(App.workspace_id).where(App.id == app_id).limit(1)
            )
        if not workspace_id:
            raise BusinessException("应用不存在", BizCode.NOT_FOUND)

        workflow_config_dict = {
            "nodes": config.nodes,
            "edges": config.edges,
            "variables": config.variables,
            "execution_config": config.execution_config,
            "features": config.features or {},
        }

        execution_context = ExecutionContext(
            execution_id=execution_id,
            workspace_id=str(workspace_id),
            user_id=str(execution.triggered_by) if execution.triggered_by else "",
            conversation_id=intervention_ctx.get("conversation_id", ""),
            memory_storage_type="neo4j",
            user_rag_memory_id="",
            checkpoint_config={
                "configurable": {
                    "thread_id": uuid.UUID(thread_id_str) if thread_id_str else uuid.uuid4(),
                }
            }
        )

        executor = WorkflowExecutor(
            workflow_config=workflow_config_dict,
            execution_context=execution_context,
        )

        graph = executor.build_graph(stream=True)

        # On resume, re-inject the sys.* variables
        # (sys.message / sys.conversation_id / ...), otherwise downstream nodes
        # resolving {{sys.*}} would get no value.
        try:
            saved_input = execution.input_data or {}
            await VariablePoolInitializer(workflow_config_dict).initialize(
                executor.variable_pool, saved_input, execution_context
            )
        except Exception as e:
            logger.warning(f"[RESUME] 重建 sys 变量失败,继续执行: {e}")

        try:
            from ..workflow.variable.variable_objects import create_variable_instance
            from ..workflow.variable.base_variable import VariableType
            from ..workflow.engine.variable_pool import VariableStruct
            current_state = (await graph.aget_state(execution_context.checkpoint_config)).values
            for nid, ninfo in (current_state.get("node_outputs") or {}).items():
                raw_output = ninfo.get("output") if isinstance(ninfo, dict) else None
                if raw_output is None:
                    continue
                if isinstance(raw_output, dict):
                    for k, v in raw_output.items():
                        inst = create_variable_instance(VariableType.STRING, str(v) if v is not None else "")
                        executor.variable_pool.variables.setdefault(nid, {})[k] = VariableStruct(type=VariableType.STRING, instance=inst, mut=True)
                else:
                    inst = create_variable_instance(VariableType.STRING, str(raw_output))
                    executor.variable_pool.variables.setdefault(nid, {})["output"] = VariableStruct(type=VariableType.STRING, instance=inst, mut=True)

            # Pre-populate StreamOutputCoordinator activation state from graph state.
            # During resume, nodes that completed BEFORE the human intervention don't
            # re-execute, so their activation signals never reach the coordinator.
            # We must replay the activation for all already-completed nodes so that
            # End nodes downstream of the intervention can properly stream output.
            executor.stream_coordinator.update_scope_activation("sys")
            activate_state = current_state.get("activate", {})
            node_outputs = current_state.get("node_outputs", {})
            for nid, is_active in activate_state.items():
                if not is_active:
                    continue
                # Determine the node's output status for branch routing
                node_output_status = executor.variable_pool.get_value(
                    f"{nid}.output", default=None, strict=False
                )
                if node_output_status is None:
                    ninfo = node_outputs.get(nid)
                    if isinstance(ninfo, dict):
                        raw_out = ninfo.get("output")
                        if isinstance(raw_out, dict):
                            node_output_status = raw_out.get("output")
                        elif raw_out is not None:
                            node_output_status = raw_out
                    # Branch nodes that use internal routing fields (__route,
                    # branch_signal) strip them from visible output. Try the
                    # variable pool as a fallback (same logic as update_stream_output_status).
                    if node_output_status is None:
                        for route_field in ("__route", "branch_signal"):
                            route_value = executor.variable_pool.get_value(
                                f"{nid}.{route_field}", default=None, strict=False
                            )
                            if route_value is not None:
                                node_output_status = route_value
                                break
                # When a branch node has no output status (e.g. an interrupted
                # HITL node), skip scope activation to avoid update_activate
                # raising RuntimeError. Such nodes are handled properly in the
                # HITL-specific pre-activation phase.
                if node_output_status is None:
                    logger.debug(
                        f"[RESUME-PREPOP] Skipping scope activation for node {nid}: "
                        f"no output status available, execution_id={execution_id}"
                    )
                    continue
                executor.stream_coordinator.update_scope_activation(nid, status=node_output_status)

            # Pre-populate _streamed_scopes for all already-completed nodes.
            # These nodes' outputs were streamed via node_chunk events in previous
            # resume cycles and consumed by their downstream End nodes. Without
            # this, emit_activate_chunk() would re-emit them as one large message
            # event from the variable pool, causing duplicate output.
            #
            # IMPORTANT: Skip human-intervention nodes. They never produce
            # node_chunk events (they use interrupt/resume instead), so their
            # output is only available via the variable pool. Marking them as
            # "streamed" would cause emit_activate_chunk to skip End node
            # variable segments that reference HITL output (e.g.
            # {{hitl.__rendered_content}}), producing no message event at all.
            from ..utils.workflow_node_types import BRANCH_NODES
            for nid, ninfo in node_outputs.items():
                if isinstance(ninfo, dict) and ninfo.get("status") == "completed":
                    raw_output = ninfo.get("output")
                    if raw_output is not None:
                        # Determine node type from workflow config to skip HITL nodes
                        node_type = None
                        for n in workflow_config_dict.get("nodes", []):
                            if n.get("id") == nid:
                                node_type = n.get("type")
                                break
                        if node_type == "human-intervention":
                            logger.debug(
                                f"[RESUME-PREPOP] Skipping _streamed_scopes for HITL node {nid}: "
                                f"no node_chunk events, output only in variable_pool, "
                                f"execution_id={execution_id}"
                            )
                            continue
                        executor.stream_coordinator.mark_scope_streamed(nid)

            # Remove already-completed End nodes from end_outputs to prevent
            # re-emitting their output (literal or variable) in this resume cycle.
            # End nodes that completed in previous cycles have already been emitted;
            # re-emitting them causes duplicate message events.
            completed_node_ids = {
                nid for nid, ninfo in node_outputs.items()
                if isinstance(ninfo, dict) and ninfo.get("status") == "completed"
            }
            removed_ends = []
            for end_id in list(executor.stream_coordinator.end_outputs.keys()):
                if end_id in completed_node_ids:
                    executor.stream_coordinator.end_outputs.pop(end_id)
                    removed_ends.append(end_id)
                    # Also clean up from queue and tracking lists
                    if end_id in executor.stream_coordinator.output_queue:
                        executor.stream_coordinator.output_queue.remove(end_id)
                    if end_id in executor.stream_coordinator.processed_outputs:
                        executor.stream_coordinator.processed_outputs.remove(end_id)
                    if executor.stream_coordinator.activate_end == end_id:
                        executor.stream_coordinator.activate_end = None

            # Set activate_end from queue after cleanup
            if executor.stream_coordinator.activate_end is None and executor.stream_coordinator.output_queue:
                executor.stream_coordinator.activate_end = executor.stream_coordinator.output_queue.popleft()

            if removed_ends:
                logger.info(
                    f"[RESUME] Removed already-completed End nodes from coordinator: "
                    f"{removed_ends}, execution_id={execution_id}"
                )

            logger.info(
                f"[RESUME] Pre-populated stream coordinator activation from graph state: "
                f"activated_nodes={sum(1 for v in activate_state.values() if v)}, "
                f"end_outputs_active={bool(executor.stream_coordinator.activate_end)}, "
                f"execution_id={execution_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to populate variable pool from graph state: {e}")

        # --- Pre-activate HITL scope and emit End node content before graph.astream ---
        # During resume, we know the action_id (user's response) so we can determine the
        # __route value before the graph executes. This pre-activates End nodes downstream
        # of the HITL node, emitting their literal content immediately — matching the
        # event ordering of LLM streaming workflows where message events arrive between
        # the predecessor node's start and end (instead of after its end).
        if action_id and resumed_node_id:
            # Determine __route from action_id and HITL node actions config
            route_value = None
            nodes_list = workflow_config_dict.get("nodes", [])
            hitl_node_cfg = next(
                (n for n in nodes_list
                 if n.get("id") == resumed_node_id
                 and n.get("type") == "human-intervention"),
                None
            )
            if action_id == "__timeout__":
                route_value = "TIMEOUT"
            else:
                if hitl_node_cfg:
                    actions_cfg = (hitl_node_cfg.get("config") or {}).get("actions", [])
                    if isinstance(actions_cfg, list):
                        action_ids = [
                            a.get("id", "") if isinstance(a, dict) else str(a)
                            for a in actions_cfg
                        ]
                        if action_id in action_ids:
                            route_value = f"CASE{action_ids.index(action_id) + 1}"

            if route_value:
                # Inject __route and __action_id into variable pool so End node
                # variable segments referencing HITL output can resolve them.
                from ..workflow.variable.variable_objects import create_variable_instance
                from ..workflow.variable.base_variable import VariableType
                from ..workflow.engine.variable_pool import VariableStruct

                route_inst = create_variable_instance(VariableType.STRING, route_value)
                executor.variable_pool.variables.setdefault(resumed_node_id, {})["__route"] = VariableStruct(
                    type=VariableType.STRING, instance=route_inst, mut=True
                )
                action_inst = create_variable_instance(VariableType.STRING, action_id)
                executor.variable_pool.variables.setdefault(resumed_node_id, {})["__action_id"] = VariableStruct(
                    type=VariableType.STRING, instance=action_inst, mut=True
                )
                # Inject form_data field values so {{hitl.field_id}} in End templates resolves
                if form_data and isinstance(form_data, dict):
                    for field_id, field_val in form_data.items():
                        field_inst = create_variable_instance(
                            VariableType.STRING, str(field_val) if field_val is not None else ""
                        )
                        executor.variable_pool.variables.setdefault(resumed_node_id, {})[field_id] = VariableStruct(
                            type=VariableType.STRING, instance=field_inst, mut=True
                        )

                # Inject __rendered_content so {{hitl.__rendered_content}} in End templates resolves.
                # The value comes from InterventionRegistry which stored it before interrupt().
                # {{form_field:id}} placeholders are replaced with actual field values
                # so the End node message shows completed content, not raw template markers.
                from ..workflow.nodes.human_intervention.node import InterventionRegistry
                hitl_registry_data = InterventionRegistry.get_all(execution_id).get(resumed_node_id) or {}
                rendered_content = hitl_registry_data.get("rendered_content", "")
                if rendered_content:
                    # Replace {{form_field:id}} with actual values (form_data or variable_ref)
                    hitl_config = ((hitl_node_cfg or {}).get("config") or {})
                    hitl_form_fields = hitl_config.get("form_fields", [])
                    for field in hitl_form_fields:
                        field_id = field.get("id")
                        placeholder = f"{{{{form_field:{field_id}}}}}"
                        if field.get("variable_ref") is not None:
                            var_ref = field.get("variable_ref")
                            field_value = executor.variable_pool.get_value(var_ref, default=None, strict=False) if var_ref else None
                        else:
                            field_value = (form_data or {}).get(field_id, field.get("default_value"))
                        replacement = str(field_value) if field_value is not None else ""
                        rendered_content = rendered_content.replace(placeholder, replacement)

                    rendered_inst = create_variable_instance(VariableType.STRING, rendered_content)
                    executor.variable_pool.variables.setdefault(resumed_node_id, {})["__rendered_content"] = VariableStruct(
                        type=VariableType.STRING, instance=rendered_inst, mut=True
                    )

                # Pre-activate HITL scope in StreamOutputCoordinator:
                #   - control_resolved = True  (route matches End's control_nodes)
                #   - output_resolved = True   (HITL removed from upstream_output_nodes)
                #   → End node becomes globally active
                executor.stream_coordinator.update_scope_activation(resumed_node_id, status=route_value)

                # Emit End node content that's now available (literals & resolved variables).
                # These arrive BEFORE HITL's node_end, consistent with LLM streaming pattern.
                async for msg_event in executor.stream_coordinator.emit_activate_chunk(executor.variable_pool):
                    yield msg_event

                logger.info(
                    f"[RESUME] Pre-activated HITL scope: node={resumed_node_id}, "
                    f"route={route_value}, execution_id={execution_id}"
                )

        start_time = utcnow_naive()
        full_content = ""
        filter_nodes = filter_node_ids if filter_node_ids is not None else {
            info.get("node_id") for info in (intervention_ctx.get("interventions") or [])
        }
        effective_pending_nodes = pending_node_ids or set()
        if kind == "pending" and resumed_node_id:
            effective_pending_nodes.discard(resumed_node_id)

        # Track remaining pending nodes for post-stream computation.
        # Only used for kind=="pending" to include service-layer tracked
        # pending nodes that aren't in the graph state anymore.
        pending_node_remaining: set[str] = set()

        # Build the appropriate Command based on kind
        if kind == "interrupt":
            resume_map = {interrupt_id: {"action_id": action_id, "form_data": form_data}}
            current_state_obj = await graph.aget_state(execution_context.checkpoint_config)
            for intr in getattr(current_state_obj, 'interrupts', ()):
                if intr.id and intr.id != interrupt_id:
                    resume_map[intr.id] = {"__pending__": True}
                    intr_value = getattr(intr, 'value', None)
                    if isinstance(intr_value, dict):
                        nid = intr_value.get("node_id", "")
                        if nid:
                            effective_pending_nodes.add(nid)
            logger.info(f"[RESUME-INTERRUPT] resume_map keys: {list(resume_map.keys())}, "
                         f"pending_nodes: {effective_pending_nodes}")

            # Inject __pre_action_id: "__pending__" into pending nodes' state.
            # When these nodes re-execute (LangGraph re-runs all interrupted tasks
            # on resume), they detect this marker and return early without calling
            # interrupt() again. This prevents them from creating new interrupts
            # that would persist in graph state and cause cycling on subsequent resumes.
            pending_state_update = {}
            if effective_pending_nodes:
                pending_state_update = {
                    "node_outputs": {
                        nid: {"__pre_action_id": "__pending__"}
                        for nid in effective_pending_nodes
                    }
                }
            command = Command(resume=resume_map, update=pending_state_update)

            # Clean up only the resumed node (the one actually resolved by the user)
            # from InterventionRegistry. Other nodes (B, C) still have active interrupts
            # in LangGraph state (they returned __pending__) and their registry data
            # must be preserved for recovery in case LangGraph loses their interrupt
            # (e.g. the 3rd parallel task cancellation bug).
            from ..workflow.nodes.human_intervention.node import InterventionRegistry as _IR
            if resumed_node_id:
                _IR.cleanup_node(execution_id, resumed_node_id)
                logger.debug(f"[RESUME-INTERRUPT] cleaned up registry node: {resumed_node_id}")
        elif kind == "pending":
            # For pending nodes, re-execute the node via Command(goto=...).
            # The node detects __pre_action_id and returns quickly (no interrupt()).
            # This produces node_start/node_end events for the frontend.
            from ..workflow.nodes.human_intervention.node import InterventionRegistry as _IR

            logger.info(
                f"[RESUME-PENDING] node {resumed_node_id} with action {action_id} "
                f"- using Command(goto=...), execution_id={execution_id}"
            )

            # Inject the real action_id into state so the node reads it on re-execution.
            # The node checks __pre_action_id: if it's not "__pending__", it uses it as
            # the action and returns without calling interrupt().
            pending_state_update = {
                "node_outputs": {
                    resumed_node_id: {"__pre_action_id": action_id, "__form_data": form_data}
                }
            }
            command = Command(goto=resumed_node_id, update=pending_state_update)

            # Clean up the pending node from InterventionRegistry
            _IR.cleanup_node(execution_id, resumed_node_id)

            # Compute remaining pending nodes (from service-layer tracking).
            # These won't appear in graph state interrupts (they were consumed
            # in the first interrupt resume cycle), so we need to track them here.
            pending_node_remaining = set(pending_node_ids) - {resumed_node_id}

        try:
            async for event in graph.astream(
                command,
                stream_mode=["updates", "debug", "custom"],
                config=execution_context.checkpoint_config,
            ):
                if isinstance(event, tuple) and len(event) == 2:
                    mode, data = event
                else:
                    continue

                if mode == "custom":
                    event_type = data.get("type", "node_chunk")
                    if event_type == "node_chunk":
                        async for msg_event in executor.event_handler.handle_node_chunk_event(data):
                            if msg_event.get("event") == "message":
                                full_content += msg_event["data"].get("content", "")
                            yield msg_event
                    elif event_type == "node_error":
                        async for error_event in executor.event_handler.handle_node_error_event(data):
                            yield error_event
                    elif event_type == "cycle_item":
                        async for cycle_event in executor.event_handler.handle_cycle_item_event(data):
                            yield cycle_event

                elif mode == "debug":
                    payload = data.get("payload", {})
                    node_name = payload.get("name")
                    event_type = data.get("type")
                    if filter_nodes and node_name in filter_nodes and event_type == "task":
                        continue
                    if node_name in effective_pending_nodes and event_type == "task":
                        continue
                    if node_name in effective_pending_nodes and event_type == "task_result":
                        continue
                    async for debug_event in executor.event_handler.handle_debug_event(
                        data, {"conversation_id": intervention_ctx.get("conversation_id", "")}
                    ):
                        yield debug_event

                elif mode == "updates":
                    filtered_data = dict(data)
                    if filter_nodes:
                        for node_id in list(filtered_data.keys()):
                            if node_id == resumed_node_id:
                                continue
                            if node_id in filter_nodes:
                                del filtered_data[node_id]
                    if effective_pending_nodes:
                        for node_id in list(filtered_data.keys()):
                            if node_id in effective_pending_nodes:
                                del filtered_data[node_id]
                    if filtered_data:
                        async for msg_event in executor.event_handler.handle_updates_event(
                            filtered_data, graph, execution_context.checkpoint_config
                        ):
                            if msg_event.get("event") == "message":
                                full_content += msg_event["data"].get("content", "")
                            yield msg_event

            logger.info(
                f"[RESUME] graph.astream() completed for kind={kind}, "
                f"resumed_node={resumed_node_id}, execution_id={execution_id}"
            )

            async for msg_event in executor.stream_coordinator.flush_remaining_chunk(executor.variable_pool):
                if msg_event.get("event") == "message":
                    full_content += msg_event["data"].get("content", "")
                yield msg_event

            graph_state = await graph.aget_state(execution_context.checkpoint_config)
            elapsed_time = (utcnow_naive() - start_time).total_seconds()
            logger.info(
                f"[RESUME] state fetched: interrupts={len(tuple(getattr(graph_state, 'interrupts', ()) or ()))}, "
                f"elapsed={elapsed_time:.2f}s, execution_id={execution_id}"
            )

            pending_intervention_info = []
            if kind == "interrupt" and effective_pending_nodes:
                for nid in effective_pending_nodes:
                    intervention_ctx_interventions = intervention_ctx.get("interventions") or []
                    ctx_match = next((i for i in intervention_ctx_interventions if i.get("node_id") == nid), None)
                    pending_intervention_info.append({
                        "node_id": nid,
                        "rendered_content": ctx_match.get("rendered_content", "") if ctx_match else "",
                        "actions": ctx_match.get("actions", []) if ctx_match else [],
                    })

            # Use top-level flat interrupts tuple (more reliable than traversing tasks)
            all_state_interrupts = tuple(getattr(graph_state, 'interrupts', ()) or ())
            remaining_interrupts = []
            for intr in all_state_interrupts:
                if kind == "interrupt" and intr.id == interrupt_id:
                    continue
                intr_value = getattr(intr, 'value', None)
                if intr_value and isinstance(intr_value, dict):
                    intr_copy = dict(intr_value)
                    intr_copy["__interrupt_id__"] = intr.id
                    remaining_interrupts.append(intr_copy)

            # Also check InterventionRegistry for lost interrupts during resume
            from ..workflow.nodes.human_intervention.node import InterventionRegistry
            all_registered = InterventionRegistry.get_all(execution_id)
            collected_node_ids = {intr.get("node_id") for intr in remaining_interrupts}
            for node_id, reg_data in all_registered.items():
                if node_id not in collected_node_ids and node_id != resumed_node_id:
                    logger.warning(
                        f"[RESUME] Recovering lost interrupt from registry: node={node_id}, "
                        f"execution_id={execution_id}"
                    )
                    synthetic = dict(reg_data)
                    synthetic["__interrupt_id__"] = ""
                    synthetic["__from_registry__"] = True
                    remaining_interrupts.append(synthetic)

            # For pending nodes: also include service-layer tracked pending nodes
            # that aren't in graph state (they were consumed in the first resume cycle).
            # Filter out resolved nodes to prevent them from reappearing.
            if pending_node_remaining:
                collected_node_ids = {intr.get("node_id") for intr in remaining_interrupts}
                intervention_ctx_interventions_all = intervention_ctx.get("interventions") or []
                for nid in pending_node_remaining:
                    if nid not in collected_node_ids:
                        ctx_intr = next(
                            (i for i in intervention_ctx_interventions_all if i.get("node_id") == nid),
                            None,
                        )
                        if ctx_intr:
                            remaining_interrupts.append({
                                "node_id": nid,
                                "rendered_content": ctx_intr.get("rendered_content", ""),
                                "actions": ctx_intr.get("actions", []),
                                "__interrupt_id__": "",
                            })
                            logger.info(
                                f"[RESUME] added pending node to remaining: {nid}, "
                                f"execution_id={execution_id}"
                            )
            # NOTE: Per-node cleanup happens when nodes are resolved (see the
            # kind=="interrupt" and kind=="pending" blocks above).  Full
            # execution-level cleanup happens only when the workflow fully
            # completes (no remaining interventions).

            # Deduplicate: pending_intervention_info may contain nodes already
            # in remaining_interrupts (e.g. when a node marked __pending__ still
            # has an active interrupt in LangGraph state). Only include pending
            # nodes that are NOT already represented in remaining_interrupts.
            remaining_node_ids = {intr.get("node_id") for intr in remaining_interrupts}
            unique_pending_info = [
                p for p in pending_intervention_info
                if p["node_id"] not in remaining_node_ids
            ]

            has_remaining = remaining_interrupts or unique_pending_info
            logger.info(
                f"[RESUME] has_remaining={bool(has_remaining)}, "
                f"remaining_interrupts={len(remaining_interrupts)}, "
                f"pending_info={len(pending_intervention_info)} -> unique={len(unique_pending_info)}, "
                f"kind={kind}, execution_id={execution_id}"
            )

            if has_remaining:
                all_interventions = []
                for intr in remaining_interrupts:
                    all_interventions.append({
                        "node_id": intr.get("node_id", ""),
                        "rendered_content": intr.get("rendered_content", ""),
                        "actions": intr.get("actions", []),
                        "interrupt_id": intr.get("__interrupt_id__", ""),
                    })
                all_interventions.extend(unique_pending_info)
                current_context = execution.context or {}
                current_intervention_ctx = current_context.get("human_intervention", {})
                new_context = {
                    **current_context,
                    "human_intervention": {
                        **current_intervention_ctx,
                        "interventions": all_interventions,
                    },
                }
                execution = await self._patch_execution_async(
                    execution_id,
                    status="waiting_human",
                    context=new_context,
                )

                # Include formatted output so that run_stream Path 2 can use it
                # when all remaining interventions are filtered as resolved.
                formatted_output = executor.result_builder.build_final_output(
                    graph_state.values, execution_context, executor.variable_pool,
                    elapsed_time, full_content, success=True
                )
                yield {
                    "event": "workflow_end",
                    "data": {
                        "status": "waiting_human",
                        "execution_id": execution_id,
                        "interventions": remaining_interrupts + [
                            {"node_id": p["node_id"], "rendered_content": p["rendered_content"], "actions": p["actions"]}
                            for p in unique_pending_info
                        ],
                        "formatted_output": formatted_output,
                    }
                }
            else:
                result = graph_state.values
                # Fallback: if streaming did not produce message events (e.g. End
                # node output was not streamed during resume), extract the End
                # node output directly from graph state node_outputs.
                if not full_content:
                    node_outputs = result.get("node_outputs", {})
                    for nid, ninfo in node_outputs.items():
                        if isinstance(ninfo, dict) and ninfo.get("node_type") == "end":
                            end_output = ninfo.get("output")
                            if isinstance(end_output, dict):
                                full_content = end_output.get("output", "")
                            elif isinstance(end_output, str):
                                full_content = end_output
                            if full_content:
                                break

                # Use build_final_output to format output_data so that the log
                # view gets a top-level "output" field. Raw graph_state.values
                # lacks it, causing _extract_text to dump the entire state as JSON.
                formatted_output = executor.result_builder.build_final_output(
                    result, execution_context, executor.variable_pool,
                    elapsed_time, full_content, success=True
                )
                execution = await self._patch_execution_async(
                    execution_id,
                    status="completed",
                    output_data=formatted_output,
                    token_usage=formatted_output.get("token_usage"),
                )

                try:
                    await self._persist_execution_artifacts_async(execution.execution_id, config)
                except Exception as persist_err:
                    logger.warning(f"Failed to persist node executions on resume completed: {persist_err}")

                # Workflow fully completed — safe to clean up the registry now.
                from ..workflow.nodes.human_intervention.node import InterventionRegistry
                InterventionRegistry.cleanup(execution_id)
                InterventionRegistry.remove_timeout(execution_id)

                # Clean up checkpointer from cache to prevent memory leak
                if thread_id_str:
                    from ..workflow.engine.graph_builder import remove_checkpointer
                    remove_checkpointer(thread_id_str)

                yield {
                    "event": "workflow_end",
                    "data": formatted_output
                }

        except Exception as e:
            logger.error(
                f"Resume workflow failed: execution_id={execution_id}, error={e}",
                exc_info=True,
            )
            try:
                recovered_state = await graph.aget_state(execution_context.checkpoint_config)
                recovered_outputs = (recovered_state.values or {}).get("node_outputs", {})
                if recovered_outputs:
                    import copy as _copy
                    from ..utils.text_sanitize import sanitize_value
                    new_output_data = _copy.deepcopy(execution.output_data or {})
                    merged = new_output_data.setdefault("node_outputs", {})
                    merged.update(sanitize_value(recovered_outputs))
                    execution.output_data = new_output_data
            except Exception as recover_err:
                logger.warning(f"Failed to recover state on resume error: {recover_err}")

            # If the error is a NodeExecutionError, inject the failed node's output
            # into node_outputs so it appears in the log (same pattern as executor.py).
            from ..workflow.nodes.base_node import NodeExecutionError
            error_node_id = None
            if isinstance(e, NodeExecutionError):
                if not execution.output_data:
                    execution.output_data = {}
                node_outputs = execution.output_data.setdefault("node_outputs", {})
                node_outputs.setdefault(e.node_id, e.node_output)
                error_node_id = e.node_id

            execution = await self._patch_execution_async(
                execution_id,
                status="failed",
                output_data=execution.output_data,
                error_message=str(e),
                error_node_id=error_node_id,
            )

            try:
                await self._persist_execution_artifacts_async(execution.execution_id, config)
            except Exception as persist_err:
                logger.warning(f"Failed to persist node executions on resume error: {persist_err}")

            # Workflow failed — clean up registry to avoid leaks.
            from ..workflow.nodes.human_intervention.node import InterventionRegistry
            InterventionRegistry.cleanup(execution_id)
            InterventionRegistry.remove_timeout(execution_id)

            yield {
                "event": "workflow_end",
                "data": {
                    "status": "failed",
                    "error": str(e),
                    "execution_id": execution_id,
                }
            }


    def _resolve_variables_file_defaults_sync(
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


# ==================== Dependency injection functions ====================

def get_workflow_service(
        db: Annotated[Session, Depends(get_db)]
) -> WorkflowService:
    """Get the workflow service (dependency injection)."""
    return WorkflowService(db)
