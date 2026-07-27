"""
Memory Configuration Service

Centralized configuration loading and management for memory services.
This service eliminates code duplication between MemoryAgentService and MemoryStorageService.
"""

import asyncio
import time
import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.logging_config import get_config_logger, get_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.core.validators.memory_config_validators import (
    validate_and_resolve_model_id,
    validate_and_resolve_model_id_async,
)
from app.i18n.service import t
from app.models import Workspace
from app.models.app_model import AppType
from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
from app.repositories.end_user_repository import get_end_user_by_id, get_end_user_by_id_async
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.ontology_class_repository import OntologyClassRepository
from app.repositories.workspace_repository import get_workspace_memory_config_id, get_workspace_memory_config_id_async
from app.schemas.memory_config_schema import (
    ConfigurationError,
    InvalidConfigError,
    MemoryConfig,
    ModelInactiveError,
    ModelNotFoundError,
)
from app.utils.redis_cache import redis_cache

logger = get_logger(__name__)
config_logger = get_config_logger()


def _validate_config_id(config_id, db: Session):
    """Validate configuration ID format (supports both UUID and integer)."""
    if isinstance(config_id, uuid.UUID):
        return config_id

    if config_id is None:
        raise InvalidConfigError(
            "Configuration ID cannot be None",
            field_name="config_id",
            invalid_value=config_id,
        )

    if isinstance(config_id, int):
        if config_id <= 0:
            raise InvalidConfigError(
                f"Configuration ID must be positive: {config_id}",
                field_name="config_id",
                invalid_value=config_id,
            )
        # 如果提供了数据库会话，尝试通过 config_id_old 查询 config_id
        if db is not None:
            # 查询 config_id_old 匹配的记录
            stmt = select(MemoryConfigModel).where(MemoryConfigModel.config_id_old == config_id)
            result = db.execute(stmt).scalars().first()
            if result:
                logger.info(f"Found config_id {result.config_id} for config_id_old {config_id}")
                return result.config_id

        raise InvalidConfigError(
            f"未找到 config_id_old={config_id} 对应的配置",
            field_name="config_id",
            invalid_value=config_id,
        )

    if isinstance(config_id, str):
        config_id_stripped = config_id.strip()

        # Try parsing as UUID first
        try:
            return uuid.UUID(config_id_stripped)
        except ValueError:
            pass

        # Fall back to integer parsing
        try:
            parsed_id = int(config_id_stripped)
            if parsed_id <= 0:
                raise InvalidConfigError(
                    f"Configuration ID must be positive: {parsed_id}",
                    field_name="config_id",
                    invalid_value=config_id,
                )

            # 如果提供了数据库会话，尝试通过 user_id 查询 config_id
            if db is not None:
                # 查询 config_id_old 匹配的记录
                stmt = select(MemoryConfigModel).where(MemoryConfigModel.config_id_old == parsed_id)
                result = db.execute(stmt).scalars().first()

                if result:
                    logger.info(f"Found config_id {result.config_id} for config_id_old {parsed_id}")
                    return result.config_id

            raise InvalidConfigError(
                f"未找到 config_id_old={parsed_id} 对应的配置",
                field_name="config_id",
                invalid_value=config_id,
            )
        except ValueError:
            raise InvalidConfigError(
                f"Invalid configuration ID format: '{config_id}' (must be UUID or positive integer)",
                field_name="config_id",
                invalid_value=config_id,
            )

    raise InvalidConfigError(
        f"Invalid type for configuration ID: expected UUID, int or str, got {type(config_id).__name__}",
        field_name="config_id",
        invalid_value=config_id,
    )


def _load_ontology_class_infos(db: Session, scene_id) -> list:
    """从 ontology_class 表加载完整本体类型信息（name + description），用于注入剪枝提示词。

    Args:
        db: 数据库会话
        scene_id: 本体场景 UUID

    Returns:
        [{"class_name": ..., "class_description": ...}, ...] 或空列表
    """
    if not scene_id:
        return []
    try:
        repo = OntologyClassRepository(db)
        classes = repo.get_classes_by_scene(scene_id)
        return [
            {"class_name": c.class_name, "class_description": c.class_description or ""}
            for c in classes if c.class_name
        ]
    except Exception as e:
        logger.warning(f"Failed to load ontology class infos for scene_id={scene_id}: {e}")
        return []


async def _load_ontology_class_infos_async(db: AsyncSession, scene_id) -> list:
    """Async version of _load_ontology_class_infos — delegates to OntologyClassRepository."""
    if not scene_id:
        return []
    try:
        repo = OntologyClassRepository(db)
        classes = await repo.get_classes_by_scene_async(scene_id)
        return [
            {"class_name": c.class_name, "class_description": c.class_description or ""}
            for c in classes if c.class_name
        ]
    except Exception as e:
        logger.warning(f"Failed to load ontology class infos for scene_id={scene_id}: {e}")
        return []


def _build_memory_config(
    memory_config_row,
    workspace,
    llm_uuid, llm_name,
    embedding_uuid, embedding_name,
    rerank_uuid, rerank_name,
    vision_uuid, vision_name,
    audio_uuid, audio_name,
    video_uuid, video_name,
    ontology_class_infos,
) -> MemoryConfig:
    """Construct a MemoryConfig from validated models — shared by sync and async paths."""
    return MemoryConfig(
        config_id=memory_config_row.config_id,
        config_name=memory_config_row.config_name,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        tenant_id=workspace.tenant_id,
        llm_model_id=llm_uuid,
        llm_model_name=llm_name,
        embedding_model_id=embedding_uuid,
        embedding_model_name=embedding_name,
        rerank_model_id=rerank_uuid,
        rerank_model_name=rerank_name,
        video_model_id=video_uuid,
        video_model_name=video_name,
        vision_model_id=vision_uuid,
        vision_model_name=vision_name,
        audio_model_id=audio_uuid,
        audio_model_name=audio_name,
        storage_type=workspace.storage_type or "neo4j",
        chunker_strategy=memory_config_row.chunker_strategy or "RecursiveChunker",
        reflexion_enabled=memory_config_row.enable_self_reflexion or False,
        reflexion_iteration_period=int(memory_config_row.iteration_period or "3"),
        reflexion_range=memory_config_row.reflexion_range or "partial",
        reflexion_baseline=memory_config_row.baseline or "Time",
        loaded_at=utcnow_naive(),
        # Pipeline config: Deduplication
        enable_llm_dedup_blockwise=bool(
            memory_config_row.enable_llm_dedup_blockwise) if memory_config_row.enable_llm_dedup_blockwise is not None else False,
        enable_llm_disambiguation=bool(
            memory_config_row.enable_llm_disambiguation) if memory_config_row.enable_llm_disambiguation is not None else False,
        deep_retrieval=bool(
            memory_config_row.deep_retrieval) if memory_config_row.deep_retrieval is not None else True,
        t_type_strict=float(
            memory_config_row.t_type_strict) if memory_config_row.t_type_strict is not None else 0.8,
        t_name_strict=float(
            memory_config_row.t_name_strict) if memory_config_row.t_name_strict is not None else 0.8,
        t_overall=float(
            memory_config_row.t_overall) if memory_config_row.t_overall is not None else 0.8,
        # Pipeline config: Statement extraction
        statement_granularity=int(
            memory_config_row.statement_granularity) if memory_config_row.statement_granularity is not None else 2,
        include_dialogue_context=bool(
            memory_config_row.include_dialogue_context) if memory_config_row.include_dialogue_context is not None else False,
        max_dialogue_context_chars=int(
            memory_config_row.max_context) if memory_config_row.max_context is not None else 1000,
        # Pipeline config: Forgetting engine
        lambda_time=float(
            memory_config_row.lambda_time) if memory_config_row.lambda_time is not None else 0.5,
        lambda_mem=float(
            memory_config_row.lambda_mem) if memory_config_row.lambda_mem is not None else 0.5,
        offset=float(memory_config_row.offset) if memory_config_row.offset is not None else 0.0,
        # Pipeline config: Pruning
        pruning_enabled=bool(
            memory_config_row.pruning_enabled) if memory_config_row.pruning_enabled is not None else False,
        pruning_scene=memory_config_row.pruning_scene or "education",
        pruning_threshold=float(
            memory_config_row.pruning_threshold) if memory_config_row.pruning_threshold is not None else 0.5,
        # Pipeline config: Emotion extraction
        emotion_enabled=bool(
            memory_config_row.emotion_enabled) if memory_config_row.emotion_enabled is not None else False,
        # Ontology scene association
        scene_id=memory_config_row.scene_id,
        ontology_class_infos=ontology_class_infos,
    )


class MemoryConfigService:
    """
    Centralized service for memory  configuration loading and validation.

    This class provides a single implementation of configuration loading logic
    that can be shared across multiple services, eliminating code duplication.

    Usage:
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(config_id)
        model_config = config_service.get_model_config(model_id)
    """

    def __init__(self, db: Session | AsyncSession):
        """Initialize the service with a database session.

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def _validate_model_with_fallback(
        self,
        model_id: str,
        model_type: str,
        workspace_default: str,
        workspace_tenant_id,
        config_id,
        workspace_id,
        required: bool = False,
    ) -> tuple:
        """Validate a model with workspace default fallback — sync variant."""
        if model_id:
            try:
                return validate_and_resolve_model_id(
                    model_id, model_type, self.db, workspace_tenant_id,
                    required=False, config_id=config_id, workspace_id=workspace_id,
                )
            except Exception as e:
                logger.warning(
                    f"{model_type} model validation failed, trying workspace default: {e}"
                )

        if workspace_default:
            try:
                result = validate_and_resolve_model_id(
                    workspace_default, model_type, self.db, workspace_tenant_id,
                    required=required, config_id=config_id, workspace_id=workspace_id,
                )
                if result[0]:
                    logger.info(f"Using workspace default {model_type} model: {workspace_default}")
                return result
            except Exception as e:
                logger.error(f"Workspace default {model_type} model also invalid: {e}")
                if required:
                    raise

        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model is required but not configured",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id,
                config_id=config_id,
                workspace_id=workspace_id,
            )

        return None, None

    async def _validate_model_with_fallback_async(
        self,
        model_id: str,
        model_type: str,
        workspace_default: str,
        workspace_tenant_id,
        config_id,
        workspace_id,
        required: bool = False,
    ) -> tuple:
        """Validate a model with workspace default fallback — async variant."""
        if model_id:
            try:
                return await validate_and_resolve_model_id_async(
                    model_id, model_type, self.db, workspace_tenant_id,
                    required=False, config_id=config_id, workspace_id=workspace_id,
                )
            except Exception as e:
                logger.warning(
                    f"{model_type} model validation failed, trying workspace default: {e}"
                )

        if workspace_default:
            try:
                result = await validate_and_resolve_model_id_async(
                    workspace_default, model_type, self.db, workspace_tenant_id,
                    required=required, config_id=config_id, workspace_id=workspace_id,
                )
                if result[0]:
                    logger.info(f"Using workspace default {model_type} model: {workspace_default}")
                return result
            except Exception as e:
                logger.error(f"Workspace default {model_type} model also invalid: {e}")
                if required:
                    raise

        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model is required but not configured",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id,
                config_id=config_id,
                workspace_id=workspace_id,
            )

        return None, None

    async def _validate_model_connectivity(
            self,
            model_id: str,
            model_type_label: str,
            tenant_id: UUID | None,
            config_id: UUID,
            workspace_id: UUID | None,
            locale: str = "zh",
    ) -> None:
        """解析模型凭证并调用 validate_model_config 验证 API 连通性。

        Args:
            model_id: 模型配置 ID
            model_type_label: 模型类型标签（llm / embedding / rerank）
            tenant_id: 租户 ID
            config_id: 记忆配置 ID（用于错误上下文）
            workspace_id: 工作空间 ID（用于错误上下文）
            locale: 语言代码（zh / en），用于 i18n 错误消息

        Raises:
            ModelNotFoundError: 模型不存在或没有可用 API 密钥
            ModelInactiveError: API 连通性验证失败
        """
        from app.services.model_service import ModelConfigService as ModelSvc
        from app.services.model_service import ModelApiKeyService

        # 1. 获取模型配置
        try:
            model_config = await ModelSvc.get_model_by_id_async(self.db, uuid.UUID(model_id), tenant_id)
        except Exception as e:
            raise ModelNotFoundError(
                model_id=model_id,
                model_type=model_type_label,
                config_id=config_id,
                workspace_id=workspace_id,
                message=t("memory_config.model.not_found_with_error", locale=locale,
                          model_type=model_type_label, model_id=model_id, error=str(e)),
            )

        # 2. 获取可用 API Key
        api_key_config = await ModelApiKeyService.get_available_api_key_async(
            self.db, model_config.id, tenant_id
        )
        if not api_key_config:
            raise ModelInactiveError(
                model_id=model_id,
                model_name=model_config.name,
                model_type=model_type_label,
                config_id=config_id,
                workspace_id=workspace_id,
                message=t("memory_config.model.no_api_key", locale=locale,
                          model_type=model_type_label, model_name=model_config.name),
            )

        # 3. 实际 API 连通性验证
        result = await ModelSvc.validate_model_config(
            self.db,
            model_name=api_key_config.model_name,
            provider=api_key_config.provider,
            api_key=api_key_config.api_key,
            api_base=api_key_config.api_base,
            model_type=model_type_label,
            is_omni=api_key_config.is_omni,
            capability=api_key_config.capability,
        )

        if not result.get("valid"):
            raise ModelInactiveError(
                model_id=model_id,
                model_name=api_key_config.model_name,
                model_type=model_type_label,
                config_id=config_id,
                workspace_id=workspace_id,
                message=t("memory_config.model.api_verify_failed", locale=locale,
                          model_type=model_type_label, model_name=api_key_config.model_name,
                          error=result.get('error', 'Unknown error')),
            )

    async def valid_config(self, config_id: uuid.UUID, locale: str = "zh") -> dict:
        """验证配置是否存在且关联模型 API 可用。

        所有模型验证失败均不阻断，统一收集到 warnings 返回前端告警。

        Args:
            config_id: 配置 UUID
            locale: 语言代码（zh / en），用于 i18n 告警消息

        Returns:
            dict: {
                "valid": True,
                "config_id": str,
                "config_name": str,
                "warnings": [{"model_type": str, "model_id": str, "message": str}, ...]
            }

        Raises:
            InvalidConfigError: 配置不存在
        """
        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel

        config = await self.db.get(MemoryConfigModel, config_id)
        if not config:
            raise InvalidConfigError(
                t("memory_config.config.not_found", locale=locale, config_id=str(config_id)),
                field_name="config_id",
                invalid_value=config_id,
            )

        workspace = await self.db.get(Workspace, config.workspace_id) if config.workspace_id else None
        tenant_id = workspace.tenant_id if workspace else None
        workspace_id = workspace.id if workspace else None

        all_models = [
            ("llm", config.llm_id, "extracted"),
            ("embedding", config.embedding_id, "extracted"),
            ("rerank", config.rerank_id, "extracted"),
            ("vision", config.vision_id, "extracted"),
            ("video", config.video_id, "extracted"),
            ("audio", config.audio_id, "extracted"),
            ("reflection", config.reflection_model_id, "reflection"),
            ("emotion", config.emotion_model_id, "emotion"),
        ]

        warnings: list[dict] = []

        for model_type, model_id, source in all_models:
            if not model_id:
                warnings.append({
                    "model_type": model_type,
                    "model_id": None,
                    "source": source,
                    "message": t("memory_config.model.not_configured", locale=locale, model_type=model_type),
                })

        _VALIDATE_AS_LLM = {"vision", "video", "audio", "reflection", "emotion"}

        async def _validate_one(model_type: str, model_id: str, source: str) -> dict | None:
            validate_type = "llm" if model_type in _VALIDATE_AS_LLM else model_type
            try:
                await self._validate_model_connectivity(
                    model_id,
                    validate_type,
                    tenant_id,
                    config_id,
                    workspace_id,
                    locale=locale
                )
                return None
            except ConfigurationError as e:
                logger.warning(
                    f"模型 {model_type} API 验证失败: {e}",
                    extra={"config_id": str(config_id), "model_type": model_type, "model_id": str(model_id)},
                )
                return {"model_type": model_type, "model_id": str(model_id), "source": source, "message": e.err_message}

        tasks = [
            _validate_one(model_type, model_id, source)
            for model_type, model_id, source in all_models
            if model_id
        ]
        if tasks:
            results = await asyncio.gather(*tasks)
            warnings += [w for w in results if w is not None]

        result: dict = {
            "valid": not bool(warnings),
            "config_id": str(config_id),
            "config_name": config.config_name,
        }
        if warnings:
            result["warnings"] = warnings

        return result

    @redis_cache(ttl=300, prefix="memory", skip_args=["self"], return_type=MemoryConfig)
    def load_memory_config(
            self,
            config_id: UUID
    ) -> MemoryConfig:
        """
        Load memory configuration from database with optional fallback.

        If config_id is provided, attempts to load that config directly.
        If config_id is None or not found and workspace_id is provided,
        falls back to the workspace's default configuration.

        Args:
            config_id: Configuration ID (UUID) from database (optional)

        Returns:
            MemoryConfig: Immutable configuration object

        Raises:
            ConfigurationError: If no valid configuration can be found
        """
        start_time = time.time()

        logger.info(f"Loading memory configuration from database: config_id={config_id}")

        try:
            # Use get_config_with_fallback if workspace_id is provided
            validated_config_id = _validate_config_id(config_id, self.db)

            memory_config = self.db.get(MemoryConfigModel, validated_config_id)

            if not memory_config:
                elapsed_ms = (time.time() - start_time) * 1000
                config_logger.error(
                    "Configuration not found in database",
                    extra={
                        "operation": "load_memory_config",
                        "config_id": str(config_id) if config_id else None,
                        "load_result": "not_found",
                        "elapsed_ms": elapsed_ms,
                    },
                )
                raise ConfigurationError(
                    f"Configuration not found: config_id={config_id}"
                )

            result = MemoryConfigRepository(self.db).get_config_with_workspace(memory_config.config_id)

            if not result:
                raise ConfigurationError(
                    f"Workspace not found for config {memory_config.config_id}"
                )

            memory_config, workspace = result

            # Step 2: Validate embedding model with workspace fallback
            embed_start = time.time()
            embedding_uuid, embedding_name = self._validate_model_with_fallback(
                memory_config.embedding_id, "embedding", workspace.embedding,
                workspace.tenant_id, validated_config_id, workspace.id,
                required=True,
            )
            embed_time = time.time() - embed_start
            logger.info(f"[PERF] Embedding validation: {embed_time:.4f}s")

            # Step 3: Resolve LLM model with workspace fallback
            llm_start = time.time()
            llm_uuid, llm_name = self._validate_model_with_fallback(
                memory_config.llm_id, "llm", workspace.llm,
                workspace.tenant_id, validated_config_id, workspace.id,
                required=True,
            )
            llm_time = time.time() - llm_start
            logger.info(f"[PERF] LLM validation: {llm_time:.4f}s")

            # Step 4: Resolve optional rerank model with workspace fallback
            rerank_start = time.time()
            rerank_uuid, rerank_name = self._validate_model_with_fallback(
                memory_config.rerank_id, "rerank", workspace.rerank,
                workspace.tenant_id, validated_config_id, workspace.id,
                required=False,
            )
            rerank_time = time.time() - rerank_start
            if memory_config.rerank_id or workspace.rerank:
                logger.info(f"[PERF] Rerank validation: {rerank_time:.4f}s")

            vision_uuid, vision_name = validate_and_resolve_model_id(
                memory_config.vision_id,
                "llm",
                self.db,
                workspace.tenant_id,
                required=False,
                config_id=validated_config_id,
                workspace_id=workspace.id,
            )

            audio_uuid, audio_name = validate_and_resolve_model_id(
                memory_config.audio_id,
                "llm",
                self.db,
                workspace.tenant_id,
                required=False,
                config_id=validated_config_id,
                workspace_id=workspace.id,
            )

            video_uuid, video_name = validate_and_resolve_model_id(
                memory_config.video_id,
                "llm",
                self.db,
                workspace.tenant_id,
                required=False,
                config_id=validated_config_id,
                workspace_id=workspace.id,
            )
            # Create immutable MemoryConfig object
            config = _build_memory_config(
                memory_config_row=memory_config,
                workspace=workspace,
                llm_uuid=llm_uuid, llm_name=llm_name,
                embedding_uuid=embedding_uuid, embedding_name=embedding_name,
                rerank_uuid=rerank_uuid, rerank_name=rerank_name,
                vision_uuid=vision_uuid, vision_name=vision_name,
                audio_uuid=audio_uuid, audio_name=audio_name,
                video_uuid=video_uuid, video_name=video_name,
                ontology_class_infos=_load_ontology_class_infos(self.db, memory_config.scene_id),
            )

            elapsed_ms = (time.time() - start_time) * 1000

            config_logger.info(
                "Memory configuration loaded successfully",
                extra={
                    "operation": "load_memory_config",
                    "config_id": validated_config_id,
                    "config_name": config.config_name,
                    "workspace_id": str(config.workspace_id),
                    "load_result": "success",
                    "elapsed_ms": elapsed_ms,
                },
            )

            logger.info(f"Memory configuration loaded successfully: {config.config_name}")
            return config

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000

            config_logger.error(
                "Failed to load memory configuration",
                extra={
                    "operation": "load_memory_config",
                    "config_id": config_id,
                    "load_result": "error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "elapsed_ms": elapsed_ms,
                },
                exc_info=True,
            )

            logger.error(f"Failed to load memory configuration {config_id}: {e}")
            if isinstance(e, (ConfigurationError, ValueError)):
                raise
            else:
                raise ConfigurationError(f"Failed to load configuration {config_id}: {e}")

    @redis_cache(ttl=300, prefix="memory", skip_args=["self"], return_type=MemoryConfig)
    async def load_memory_config_async(self, config_id: UUID) -> MemoryConfig:
        """Async version of load_memory_config — uses true async DB calls via AsyncSession.

        Mirrors the sync version's logic (model validation, workspace fallback, etc.)
        but with ``await`` on every DB operation so the event loop is never blocked.
        """
        start_time = time.perf_counter()
        logger.info(f"Loading memory configuration from database: config_id={config_id}")

        try:
            # Step 1: load config row + workspace in a single JOIN query
            result = await MemoryConfigRepository(self.db).get_config_with_workspace_async(config_id)
            if not result:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                config_logger.error(
                    "Configuration not found in database",
                    extra={
                        "operation": "load_memory_config_async",
                        "config_id": str(config_id),
                        "load_result": "not_found",
                        "elapsed_ms": elapsed_ms,
                    },
                )
                raise ConfigurationError(
                    f"Configuration not found: config_id={config_id}"
                )
            memory_config_row, workspace = result

            # Step 2: validate all models + load ontology concurrently
            v_start = time.time()
            (
                (embedding_uuid, embedding_name),
                (llm_uuid, llm_name),
                (rerank_uuid, rerank_name),
                (vision_uuid, vision_name),
                (audio_uuid, audio_name),
                (video_uuid, video_name),
                ontology_class_infos,
            ) = await asyncio.gather(
                self._validate_model_with_fallback_async(
                    memory_config_row.embedding_id, "embedding", workspace.embedding,
                    workspace.tenant_id, memory_config_row.config_id, workspace.id,
                    required=True,
                ),
                self._validate_model_with_fallback_async(
                    memory_config_row.llm_id, "llm", workspace.llm,
                    workspace.tenant_id, memory_config_row.config_id, workspace.id,
                    required=True,
                ),
                self._validate_model_with_fallback_async(
                    memory_config_row.rerank_id, "rerank", workspace.rerank,
                    workspace.tenant_id, memory_config_row.config_id, workspace.id,
                    required=False,
                ),
                validate_and_resolve_model_id_async(
                    memory_config_row.vision_id, "llm", self.db, workspace.tenant_id,
                    required=False, config_id=memory_config_row.config_id, workspace_id=workspace.id,
                ),
                validate_and_resolve_model_id_async(
                    memory_config_row.audio_id, "llm", self.db, workspace.tenant_id,
                    required=False, config_id=memory_config_row.config_id, workspace_id=workspace.id,
                ),
                validate_and_resolve_model_id_async(
                    memory_config_row.video_id, "llm", self.db, workspace.tenant_id,
                    required=False, config_id=memory_config_row.config_id, workspace_id=workspace.id,
                ),
                _load_ontology_class_infos_async(self.db, memory_config_row.scene_id),
            )
            v_time = time.time() - v_start
            logger.info(f"[PERF] All model validations + ontology load: {v_time:.4f}s (concurrent)")

            # Step 4: build the immutable MemoryConfig
            config = _build_memory_config(
                memory_config_row=memory_config_row,
                workspace=workspace,
                llm_uuid=llm_uuid, llm_name=llm_name,
                embedding_uuid=embedding_uuid, embedding_name=embedding_name,
                rerank_uuid=rerank_uuid, rerank_name=rerank_name,
                vision_uuid=vision_uuid, vision_name=vision_name,
                audio_uuid=audio_uuid, audio_name=audio_name,
                video_uuid=video_uuid, video_name=video_name,
                ontology_class_infos=ontology_class_infos,
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            config_logger.info(
                "Memory configuration loaded successfully",
                extra={
                    "operation": "load_memory_config_async",
                    "config_id": str(memory_config_row.config_id),
                    "config_name": config.config_name,
                    "workspace_id": str(config.workspace_id),
                    "load_result": "success",
                    "elapsed_ms": elapsed_ms,
                },
            )

            logger.info(f"Memory configuration loaded successfully: {config.config_name}")
            return config

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            config_logger.error(
                "Failed to load memory configuration",
                extra={
                    "operation": "load_memory_config_async",
                    "config_id": str(config_id),
                    "load_result": "error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "elapsed_ms": elapsed_ms,
                },
                exc_info=True,
            )

            logger.error(f"Failed to load memory configuration {config_id}: {e}")
            if isinstance(e, (ConfigurationError, ValueError)):
                raise
            else:
                raise ConfigurationError(f"Failed to load configuration {config_id}: {e}")

    def get_model_config(self, model_id: str, tenant_id: UUID | None = None) -> dict:
        """Get LLM model configuration by ID.
        
        Args:
            model_id: Model ID to look up
            tenant_id: 当前租户 ID，用于解析公共 SpeedBear 模型运行时 key
            
        Returns:
            Dict with model configuration including api_key, base_url, etc.
        """
        from fastapi import status
        from fastapi.exceptions import HTTPException

        from app.core.config import settings
        from app.services.model_service import ModelConfigService as ModelSvc
        from app.services.model_service import ModelApiKeyService

        config = ModelSvc.get_model_by_id(db=self.db, model_id=model_id, tenant_id=tenant_id)
        if not config:
            logger.warning(f"Model ID {model_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型ID不存在")

        api_config = ModelApiKeyService.get_available_api_key(
            self.db,
            config.id,
            tenant_id=tenant_id,
        )
        if not api_config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型没有可用的API密钥")

        return {
            "model_name": api_config.model_name,
            "provider": api_config.provider,
            "api_key": api_config.api_key,
            "capability": api_config.capability,
            "base_url": api_config.api_base,
            "model_config_id": str(config.id),
            "type": config.type,
            "timeout": settings.LLM_TIMEOUT,
            "max_retries": settings.LLM_MAX_RETRIES,
            "is_omni": api_config.is_omni,
        }

    def get_embedder_config(self, embedding_id: str, tenant_id: UUID | None = None) -> dict:
        """Get embedding model configuration by ID.
        
        Args:
            embedding_id: Embedding model ID to look up
            tenant_id: 当前租户 ID，用于解析公共 SpeedBear 模型运行时 key
            
        Returns:
            Dict with embedder configuration including api_key, base_url, etc.
        """
        from fastapi import status
        from fastapi.exceptions import HTTPException

        from app.services.model_service import ModelConfigService as ModelSvc
        from app.services.model_service import ModelApiKeyService

        config = ModelSvc.get_model_by_id(db=self.db, model_id=embedding_id)
        if not config:
            logger.warning(f"Embedding model ID {embedding_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="嵌入模型ID不存在")

        api_config = ModelApiKeyService.get_available_api_key(
            self.db,
            config.id,
            tenant_id=tenant_id,
        )
        if not api_config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="嵌入模型没有可用的API密钥")

        return {
            "model_name": api_config.model_name,
            "provider": api_config.provider,
            "api_key": api_config.api_key,
            "base_url": api_config.api_base,
            "model_config_id": str(config.id),
            "type": config.type,
            "timeout": 120.0,
            "max_retries": 5,
        }

    @staticmethod
    def get_pipeline_config(memory_config: MemoryConfig):
        """Build ExtractionPipelineConfig from MemoryConfig.

        Args:
            memory_config: MemoryConfig object containing all pipeline settings.

        Returns:
            ExtractionPipelineConfig with deduplication, statement extraction,
            and forgetting engine settings.
        """
        from app.core.memory.models.variate_config import (
            DedupConfig,
            ExtractionPipelineConfig,
            ForgettingEngineConfig,
            StatementExtractionConfig,
        )

        dedup_config = DedupConfig(
            enable_llm_dedup_blockwise=memory_config.enable_llm_dedup_blockwise,
            enable_llm_disambiguation=memory_config.enable_llm_disambiguation,
            fuzzy_name_threshold_strict=memory_config.t_name_strict,
            fuzzy_type_threshold_strict=memory_config.t_type_strict,
            fuzzy_overall_threshold=memory_config.t_overall,
        )

        stmt_config = StatementExtractionConfig(
            statement_granularity=memory_config.statement_granularity,
            include_dialogue_context=memory_config.include_dialogue_context,
            max_dialogue_context_chars=memory_config.max_dialogue_context_chars,
        )

        forget_config = ForgettingEngineConfig(
            offset=memory_config.offset,
            lambda_time=memory_config.lambda_time,
            lambda_mem=memory_config.lambda_mem,
        )

        return ExtractionPipelineConfig(
            statement_extraction=stmt_config,
            deduplication=dedup_config,
            forgetting_engine=forget_config,
            emotion_enabled=getattr(memory_config, "emotion_enabled", False),
        )

    @staticmethod
    def get_pruning_config(memory_config: MemoryConfig) -> dict:
        """Retrieve semantic pruning config from MemoryConfig.

        Args:
            memory_config: MemoryConfig object containing pruning settings.

        Returns:
            Dict suitable for PruningConfig.model_validate with keys:
            - pruning_switch: bool
            - pruning_scene: str
            - pruning_threshold: float
            - ontology_class_infos: list of {class_name, class_description} dicts
        """
        return {
            "pruning_switch": memory_config.pruning_enabled,
            "pruning_scene": memory_config.pruning_scene,
            "pruning_threshold": memory_config.pruning_threshold,
            "ontology_class_infos": memory_config.ontology_class_infos or [],
        }

    def get_ontology_types(self, memory_config: MemoryConfig):
        """Fetch ontology types for the memory configuration's scene.
        
        Args:
            memory_config: MemoryConfig object containing scene_id
            
        Returns:
            OntologyTypeList if scene_id is valid and has types, None otherwise
        """
        from app.core.memory.models.ontology_extraction_models import OntologyTypeList
        from app.repositories.ontology_class_repository import OntologyClassRepository

        if not memory_config.scene_id:
            logger.debug("No scene_id configured, skipping ontology type fetch")
            return None

        try:
            ontology_repo = OntologyClassRepository(self.db)
            ontology_classes = ontology_repo.get_classes_by_scene(memory_config.scene_id)

            if not ontology_classes:
                logger.info(f"No ontology classes found for scene_id: {memory_config.scene_id}")
                return None

            ontology_types = OntologyTypeList.from_db_models(ontology_classes)
            logger.info(
                f"Loaded {len(ontology_types.types)} ontology types for scene_id: {memory_config.scene_id}"
            )
            return ontology_types

        except Exception as e:
            logger.warning(
                f"Failed to fetch ontology types for scene_id {memory_config.scene_id}: {e}",
                exc_info=True
            )
            return None

    def create_workspace_default_config(
            self,
            workspace: Workspace,
            scene_id: uuid.UUID | None = None,
            pruing_scene_name: str | None = None,
    ):
        from app.models.memory_config_model import MemoryConfig as DBMemoryConfig
        config_id = uuid.uuid4()

        default_config = DBMemoryConfig(
            config_id=config_id,
            config_name=f"{workspace.name} 默认配置",
            config_desc="工作空间创建时自动生成的默认记忆配置",
            workspace_id=workspace.id,
            llm_id=str(workspace.llm) if workspace.llm else None,
            reflection_model_id=str(workspace.llm) if workspace.llm else None,
            embedding_id=str(workspace.embedding) if workspace.embedding else None,
            rerank_id=str(workspace.rerank) if workspace.rerank else None,
            vision_id=str(workspace.vision) if workspace.vision else None,
            audio_id=str(workspace.audio) if workspace.audio else None,
            video_id=str(workspace.video) if workspace.video else None,
            scene_id=scene_id,  # 关联本体场景ID（默认为"在线教育"场景）
            pruning_scene=pruing_scene_name,  # 语义剪枝场景直接使用 scene_name
            state=True,  # Active by default
            is_default=True,  # Mark as workspace default
        )

        self.db.add(default_config)
        self.db.flush()
        self.db.refresh(default_config)
        config_logger.info(
            "Created default memory config for workspace",
            extra={
                "workspace_id": str(workspace.id),
                "config_id": str(config_id),
                "config_name": default_config.config_name,
                "scene_id": str(scene_id) if scene_id else None,
            }
        )
        return config_id

    def get_workspace_default_config(
            self,
            workspace_id: UUID
    ) -> Optional["MemoryConfigModel"]:
        """Get workspace default memory config.
        
        Returns the config marked as default for the workspace. If no explicit
        default exists, falls back to the first active config ordered by creation time.
        
        Args:
            workspace_id: Workspace ID
            
        Returns:
            Optional[MemoryConfigModel]: Default config or None if no configs exist
        """
        config = MemoryConfigRepository(self.db).get_workspace_default(workspace_id)

        if not config:
            logger.warning(
                "No active memory config found for workspace fallback",
                extra={"workspace_id": str(workspace_id)}
            )

        return config

    def get_workspace_active_config_id(
            self,
            workspace_id: UUID
    ) -> uuid.UUID:
        config_id = get_workspace_memory_config_id(self.db, workspace_id)
        if not config_id:
            raise BusinessException(f"空间{workspace_id}无启用的记忆配置")
        return config_id

    async def get_workspace_active_config_id_async(
            self,
            workspace_id: uuid.UUID,
    ) -> uuid.UUID:
        config_id = await get_workspace_memory_config_id_async(self.db, workspace_id)
        if not config_id:
            raise BusinessException(f"空间{workspace_id}无启用的记忆配置")
        return config_id

    def get_config_id_by_end_user(
            self,
            end_user_id: uuid.UUID | str,
    ) -> uuid.UUID:
        if isinstance(end_user_id, str):
            end_user_id = uuid.UUID(end_user_id)

        end_user = get_end_user_by_id(self.db, end_user_id)
        config_id = self.get_workspace_active_config_id(end_user.workspace_id)
        return config_id

    async def get_config_id_by_end_user_async(
            self,
            end_user_id: uuid.UUID | str,
    ):
        if isinstance(end_user_id, str):
            end_user_id = uuid.UUID(end_user_id)
        end_user = await get_end_user_by_id_async(self.db, end_user_id)
        config_id = await self.get_workspace_active_config_id_async(end_user.workspace_id)
        return config_id

    def get_config_with_fallback(
            self,
            memory_config_id: Optional[UUID],
            workspace_id: UUID
    ) -> Optional["MemoryConfigModel"]:
        """Get memory config with fallback to workspace default.
        
        Implements graceful degradation: if the provided config_id is None or
        the config doesn't exist, falls back to the workspace's default config.
        
        Args:
            memory_config_id: Memory config ID (can be None)
            workspace_id: Workspace ID for fallback lookup
            
        Returns:
            Optional[MemoryConfigModel]: Memory config or None if no fallback available
        """
        if not memory_config_id:
            logger.debug(
                "No memory config ID provided, using workspace default",
                extra={"workspace_id": str(workspace_id)}
            )

        config = MemoryConfigRepository(self.db).get_with_fallback(
            memory_config_id,
            workspace_id
        )

        if not config and memory_config_id:
            logger.warning(
                "Memory config not found, falling back to workspace default",
                extra={
                    "missing_config_id": str(memory_config_id),
                    "workspace_id": str(workspace_id)
                }
            )

        return config

    def delete_config(
            self,
            config_id: UUID | int,
            workspace_id: uuid.UUID,
    ) -> dict:
        """Delete memory config with protection against in-use configs.
        
        Implements delete protection: prevents accidental deletion of configs
        that are actively being used by end users or marked as default.
        
        Args:
            workspace_id:
            config_id: Memory config ID to delete (UUID or legacy int)
            
        Returns:
            Dict with status, message, and affected_users count
            
        Raises:
            ResourceNotFoundException: If config doesn't exist
        """
        from sqlalchemy.exc import IntegrityError

        from app.core.exceptions import ResourceNotFoundException
        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel

        # 处理旧格式 int 类型的 config_id
        if isinstance(config_id, int):
            logger.warning(
                "Attempted to delete legacy int config_id",
                extra={"config_id": config_id}
            )
            return {
                "status": "error",
                "message": "旧格式配置ID不支持删除操作，请使用新版配置",
                "legacy_int_id": config_id
            }

        config = self.db.get(MemoryConfigModel, config_id)
        if not config:
            raise ResourceNotFoundException("MemoryConfig", str(config_id))

        # Check if this is the default config - default configs cannot be deleted
        if config.is_default:
            logger.warning(
                "Attempted to delete default memory config",
                extra={"config_id": str(config_id)}
            )
            return {
                "status": "error",
                "message": "默认配置不允许删除",
                "is_default": True
            }
        active_config_id = self.get_workspace_active_config_id(workspace_id)

        if str(config.config_id) == str(active_config_id):
            logger.warning(
                "Attempted to delete memory config with connected end users",
                extra={
                    "config_id": str(config_id),
                }
            )

            return {
                "status": "warning",
                "message": f"无法删除记忆配置：当前空间正在使用此配置",
                "force_required": True
            }

        try:
            self.db.delete(config)
            self.db.commit()

            logger.info(
                "Memory config deleted",
                extra={
                    "config_id": str(config_id),
                }
            )

            return {
                "status": "success",
                "message": "记忆配置删除成功",
            }

        except IntegrityError as e:
            self.db.rollback()

            # Handle foreign key violation gracefully
            error_str = str(e.orig) if e.orig else str(e)
            if "ForeignKeyViolation" in error_str or "foreign key constraint" in error_str.lower():
                logger.warning(
                    "Delete failed due to foreign key constraint",
                    extra={
                        "config_id": str(config_id),
                        "error": error_str
                    }
                )
                return {
                    "status": "error",
                    "message": "无法删除记忆配置：仍有终端用户引用此配置，请使用 force=true 强制删除",
                    "force_required": True
                }

            # Re-raise other integrity errors
            logger.error(
                "Delete failed due to integrity error",
                extra={
                    "config_id": str(config_id),
                    "error": error_str
                },
                exc_info=True
            )
            raise

    async def delete_config_async(
            self,
            config_id: UUID | int,
            workspace_id: uuid.UUID,
    ) -> dict:
        """Async version of delete_config — uses await for AsyncSession operations.

        Args:
            workspace_id:
            config_id: Memory config ID to delete (UUID or legacy int)

        Returns:
            Dict with status, message, and affected_users count

        Raises:
            ResourceNotFoundException: If config doesn't exist
        """
        from sqlalchemy.exc import IntegrityError

        from app.core.exceptions import ResourceNotFoundException
        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel

        # 处理旧格式 int 类型的 config_id
        if isinstance(config_id, int):
            logger.warning(
                "Attempted to delete legacy int config_id",
                extra={"config_id": config_id}
            )
            return {
                "status": "error",
                "message": "旧格式配置ID不支持删除操作，请使用新版配置",
                "legacy_int_id": config_id
            }

        config = await self.db.get(MemoryConfigModel, config_id)
        if not config:
            raise ResourceNotFoundException("MemoryConfig", str(config_id))

        # Check if this is the default config - default configs cannot be deleted
        if config.is_default:
            logger.warning(
                "Attempted to delete default memory config",
                extra={"config_id": str(config_id)}
            )
            return {
                "status": "error",
                "message": "默认配置不允许删除",
                "is_default": True
            }
        active_config_id = await self.get_workspace_active_config_id_async(workspace_id)

        if str(config.config_id) == str(active_config_id):
            logger.warning(
                "Attempted to delete memory config with connected end users",
                extra={
                    "config_id": str(config_id),
                }
            )

            return {
                "status": "warning",
                "message": "无法删除记忆配置：当前空间正在使用此配置",
                "force_required": True
            }

        try:
            await self.db.delete(config)
            await self.db.commit()

            logger.info(
                "Memory config deleted",
                extra={
                    "config_id": str(config_id),
                }
            )

            return {
                "status": "success",
                "message": "记忆配置删除成功",
            }

        except IntegrityError as e:
            await self.db.rollback()

            # Handle foreign key violation gracefully
            error_str = str(e.orig) if e.orig else str(e)
            if "ForeignKeyViolation" in error_str or "foreign key constraint" in error_str.lower():
                logger.warning(
                    "Delete failed due to foreign key constraint",
                    extra={
                        "config_id": str(config_id),
                        "error": error_str
                    }
                )
                return {
                    "status": "error",
                    "message": "无法删除记忆配置：仍有终端用户引用此配置，请使用 force=true 强制删除",
                    "force_required": True
                }

            # Re-raise other integrity errors
            logger.error(
                "Delete failed due to integrity error",
                extra={
                    "config_id": str(config_id),
                    "error": error_str
                },
                exc_info=True
            )
            raise

    # ==================== 记忆配置提取方法 ====================

    def extract_memory_config_id(
            self,
            app_type: str,
            config: dict
    ) -> tuple[Optional[uuid.UUID], bool]:
        """从发布配置中提取 memory_config_id（根据应用类型分发）
        
        Args:
            app_type: 应用类型 (agent, workflow, multi_agent)
            config: 发布配置字典
            
        Returns:
            Tuple[Optional[uuid.UUID], bool]: (memory_config_id, is_legacy_int)
                - memory_config_id: 提取的配置ID，如果不存在或为旧格式则返回 None
                - is_legacy_int: 是否检测到旧格式 int 数据，需要回退到工作空间默认配置
        """
        if app_type == AppType.AGENT:
            return self._extract_memory_config_id_from_agent(config)
        elif app_type in (AppType.WORKFLOW, AppType.PURE_WORKFLOW):
            return None, False
        elif app_type == AppType.MULTI_AGENT:
            # Multi-agent 暂不支持记忆配置提取
            logger.debug(f"多智能体应用暂不支持记忆配置提取: app_type={app_type}")
            return None, False
        else:
            logger.warning(f"不支持的应用类型，无法提取记忆配置: app_type={app_type}")
            return None, False

    def _resolve_config_id_old(self, config_id_old: int) -> Optional[uuid.UUID]:
        """通过 config_id_old 查询对应的 UUID config_id。

        Args:
            config_id_old: 旧格式的整数配置ID

        Returns:
            对应的 UUID config_id，未找到返回 None
        """
        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
        result = self.db.query(MemoryConfigModel).filter(
            MemoryConfigModel.config_id_old == config_id_old
        ).first()
        if result:
            return result.config_id
        return None

    def _extract_memory_config_id_from_agent(
            self,
            config: dict
    ) -> tuple[Optional[uuid.UUID], bool]:
        """从 Agent 应用配置中提取 memory_config_id
        
        路径: config.memory.memory_content 或 config.memory.memory_config_id
        
        Args:
            config: Agent 配置字典
            
        Returns:
            Tuple[Optional[uuid.UUID], bool]: (memory_config_id, is_legacy_int)
                - memory_config_id: 记忆配置ID，如果不存在或为旧格式则返回 None
                - is_legacy_int: 是否检测到旧格式 int 数据
        """
        try:
            memory_dict = config.get("memory", {})
            # Support both field names: memory_config_id (new) and memory_content (legacy)
            memory_value = memory_dict.get("memory_config_id") or memory_dict.get("memory_content")
            logger.debug(
                f"Extracting memory_config_id: memory_value={memory_value}, "
                f"type={type(memory_value).__name__ if memory_value else 'None'}"
            )
            if memory_value:
                # 处理字符串、UUID 和 int（旧数据兼容）三种情况
                if isinstance(memory_value, uuid.UUID):
                    return memory_value, False
                elif isinstance(memory_value, str):
                    # Check if it's a numeric string (legacy int format)
                    if memory_value.isdigit():
                        resolved = self._resolve_config_id_old(int(memory_value))
                        if resolved:
                            logger.info(f"Resolved legacy config_id_old={memory_value} to config_id={resolved}")
                            return resolved, False
                        logger.warning(f"未找到 config_id_old={memory_value} 对应的配置，将使用工作空间默认配置")
                        return None, True
                    try:
                        return uuid.UUID(memory_value), False
                    except ValueError:
                        logger.warning(f"Invalid UUID string: {memory_value}")
                        return None, False
                elif isinstance(memory_value, int):
                    resolved = self._resolve_config_id_old(memory_value)
                    if resolved:
                        logger.info(f"Resolved legacy config_id_old={memory_value} to config_id={resolved}")
                        return resolved, False
                    logger.warning(f"未找到 config_id_old={memory_value} 对应的配置，将使用工作空间默认配置")
                    return None, True
                else:
                    logger.warning(
                        f"Agent 配置中 memory_config_id 格式无效: type={type(memory_value)}, "
                        f"value={memory_value}"
                    )
            return None, False
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Agent 配置中 memory_config_id 格式无效: error={str(e)}"
            )
            return None, False
