"""
Memory Configuration Service

Centralized configuration loading and management for memory services.
This service eliminates code duplication between MemoryAgentService and MemoryStorageService.
"""

import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging_config import get_config_logger, get_logger
from app.core.validators.memory_config_validators import (
    validate_and_resolve_model_id,
    validate_embedding_model,
)
from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.memory_config_schema import (
    ConfigurationError,
    InvalidConfigError,
    MemoryConfig,
)

if TYPE_CHECKING:
    from app.models.memory_config_model import MemoryConfig as MemoryConfigModel

logger = get_logger(__name__)
config_logger = get_config_logger()


def _validate_config_id(config_id, db: Session = None):
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
        # 如果提供了数据库会话，尝试通过 user_id 查询 config_id
        if db is not None:
            # 查询 user_id 匹配的记录
            stmt = select(MemoryConfigModel).where(MemoryConfigModel.config_id_old == str(config_id))
            result = db.execute(stmt).scalars().first()
            if result:
                logger.info(f"Found config_id {result.config_id} for user_id {config_id}")
                return result.config_id
        
        return config_id

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
                # 查询 user_id 匹配的记录
                stmt = select(MemoryConfigModel).where(MemoryConfigModel.user_id == str(parsed_id))
                result = db.execute(stmt).scalars().first()
                
                if result:
                    logger.info(f"Found config_id {result.config_id} for user_id {parsed_id}")
                    return result.config_id
            
            return parsed_id
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


class MemoryConfigService:
    """
    Centralized service for memory configuration loading and validation.

    This class provides a single implementation of configuration loading logic
    that can be shared across multiple services, eliminating code duplication.

    Usage:
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(config_id)
        model_config = config_service.get_model_config(model_id)
    """

    def __init__(self, db: Session):
        """Initialize the service with a database session.

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def load_memory_config(
        self,
        config_id: UUID,
        service_name: str = "MemoryConfigService",
    ) -> MemoryConfig:
        """
        Load memory configuration from database by config_id.

        Args:
            config_id: Configuration ID (UUID) from database
            service_name: Name of the calling service (for logging purposes)

        Returns:
            MemoryConfig: Immutable configuration object

        Raises:
            ConfigurationError: If validation fails
        """
        start_time = time.time()

        config_logger.info(
            "Starting memory configuration loading",
            extra={
                "operation": "load_memory_config",
                "service": service_name,
                "config_id": str(config_id),
            },
        )

        logger.info(f"Loading memory configuration from database: config_id={config_id}")

        try:
            validated_config_id = _validate_config_id(config_id, self.db)

            # Step 1: Get config and workspace
            db_query_start = time.time()
            result = MemoryConfigRepository.get_config_with_workspace(self.db, validated_config_id)
            db_query_time = time.time() - db_query_start
            logger.info(f"[PERF] Config+Workspace query: {db_query_time:.4f}s")
            if not result:
                elapsed_ms = (time.time() - start_time) * 1000
                config_logger.error(
                    "Configuration not found in database",
                    extra={
                        "operation": "load_memory_config",
                        "config_id": str(config_id),
                        "load_result": "not_found",
                        "elapsed_ms": elapsed_ms,
                        "service": service_name,
                    },
                )
                raise ConfigurationError(
                    f"Configuration {config_id} not found in database"
                )

            memory_config, workspace = result

            # Step 2: Validate embedding model (returns both UUID and name)
            embed_start = time.time()
            embedding_uuid, embedding_name = validate_embedding_model(
                validated_config_id,
                memory_config.embedding_id,
                self.db,
                workspace.tenant_id,
                workspace.id,
            )
            embed_time = time.time() - embed_start
            logger.info(f"[PERF] Embedding validation: {embed_time:.4f}s")

            # Step 3: Resolve LLM model
            llm_start = time.time()
            llm_uuid, llm_name = validate_and_resolve_model_id(
                memory_config.llm_id,
                "llm",
                self.db,
                workspace.tenant_id,
                required=True,
                config_id=validated_config_id,
                workspace_id=workspace.id,
            )
            llm_time = time.time() - llm_start
            logger.info(f"[PERF] LLM validation: {llm_time:.4f}s")

            # Step 4: Resolve optional rerank model
            rerank_start = time.time()
            rerank_uuid = None
            rerank_name = None
            if memory_config.rerank_id:
                rerank_uuid, rerank_name = validate_and_resolve_model_id(
                    memory_config.rerank_id,
                    "rerank",
                    self.db,
                    workspace.tenant_id,
                    required=False,
                    config_id=validated_config_id,
                    workspace_id=workspace.id,
                )
            rerank_time = time.time() - rerank_start
            if memory_config.rerank_id:
                logger.info(f"[PERF] Rerank validation: {rerank_time:.4f}s")

            # Note: embedding_name is now returned from validate_embedding_model above
            # No need for redundant query!

            # Create immutable MemoryConfig object
            config = MemoryConfig(
                config_id=memory_config.config_id,
                config_name=memory_config.config_name,
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                tenant_id=workspace.tenant_id,
                llm_model_id=llm_uuid,
                llm_model_name=llm_name,
                embedding_model_id=embedding_uuid,
                embedding_model_name=embedding_name,
                rerank_model_id=rerank_uuid,
                rerank_model_name=rerank_name,
                storage_type=workspace.storage_type or "neo4j",
                chunker_strategy=memory_config.chunker_strategy or "RecursiveChunker",
                reflexion_enabled=memory_config.enable_self_reflexion or False,
                reflexion_iteration_period=int(memory_config.iteration_period or "3"),
                reflexion_range=memory_config.reflexion_range or "partial",
                reflexion_baseline=memory_config.baseline or "Time",
                loaded_at=datetime.now(),
                # Pipeline config: Deduplication
                enable_llm_dedup_blockwise=bool(memory_config.enable_llm_dedup_blockwise) if memory_config.enable_llm_dedup_blockwise is not None else False,
                enable_llm_disambiguation=bool(memory_config.enable_llm_disambiguation) if memory_config.enable_llm_disambiguation is not None else False,
                deep_retrieval=bool(memory_config.deep_retrieval) if memory_config.deep_retrieval is not None else True,
                t_type_strict=float(memory_config.t_type_strict) if memory_config.t_type_strict is not None else 0.8,
                t_name_strict=float(memory_config.t_name_strict) if memory_config.t_name_strict is not None else 0.8,
                t_overall=float(memory_config.t_overall) if memory_config.t_overall is not None else 0.8,
                # Pipeline config: Statement extraction
                statement_granularity=int(memory_config.statement_granularity) if memory_config.statement_granularity is not None else 2,
                include_dialogue_context=bool(memory_config.include_dialogue_context) if memory_config.include_dialogue_context is not None else False,
                max_dialogue_context_chars=int(memory_config.max_context) if memory_config.max_context is not None else 1000,
                # Pipeline config: Forgetting engine
                lambda_time=float(memory_config.lambda_time) if memory_config.lambda_time is not None else 0.5,
                lambda_mem=float(memory_config.lambda_mem) if memory_config.lambda_mem is not None else 0.5,
                offset=float(memory_config.offset) if memory_config.offset is not None else 0.0,
                # Pipeline config: Pruning
                pruning_enabled=bool(memory_config.pruning_enabled) if memory_config.pruning_enabled is not None else False,
                pruning_scene=memory_config.pruning_scene or "education",
                pruning_threshold=float(memory_config.pruning_threshold) if memory_config.pruning_threshold is not None else 0.5,
            )

            elapsed_ms = (time.time() - start_time) * 1000

            config_logger.info(
                "Memory configuration loaded successfully",
                extra={
                    "operation": "load_memory_config",
                    "service": service_name,
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
                    "service": service_name,
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

    def get_model_config(self, model_id: str) -> dict:
        """Get LLM model configuration by ID.
        
        Args:
            model_id: Model ID to look up
            
        Returns:
            Dict with model configuration including api_key, base_url, etc.
        """
        from fastapi import status
        from fastapi.exceptions import HTTPException

        from app.core.config import settings
        from app.models.models_model import ModelApiKey
        from app.services.model_service import ModelConfigService as ModelSvc

        config = ModelSvc.get_model_by_id(db=self.db, model_id=model_id)
        if not config:
            logger.warning(f"Model ID {model_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型ID不存在")
        
        api_config: ModelApiKey = config.api_keys[0]
        
        return {
            "model_name": api_config.model_name,
            "provider": api_config.provider,
            "api_key": api_config.api_key,
            "base_url": api_config.api_base,
            "model_config_id": str(config.id),
            "type": config.type,
            "timeout": settings.LLM_TIMEOUT,
            "max_retries": settings.LLM_MAX_RETRIES,
        }

    def get_embedder_config(self, embedding_id: str) -> dict:
        """Get embedding model configuration by ID.
        
        Args:
            embedding_id: Embedding model ID to look up
            
        Returns:
            Dict with embedder configuration including api_key, base_url, etc.
        """
        from fastapi import status
        from fastapi.exceptions import HTTPException

        from app.models.models_model import ModelApiKey
        from app.services.model_service import ModelConfigService as ModelSvc

        config = ModelSvc.get_model_by_id(db=self.db, model_id=embedding_id)
        if not config:
            logger.warning(f"Embedding model ID {embedding_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="嵌入模型ID不存在")
        
        api_config: ModelApiKey = config.api_keys[0]
        
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
        """
        return {
            "pruning_switch": memory_config.pruning_enabled,
            "pruning_scene": memory_config.pruning_scene,
            "pruning_threshold": memory_config.pruning_threshold,
        }

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
        from sqlalchemy import select

        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
        
        # First, try to find the explicitly marked default config
        stmt = (
            select(MemoryConfigModel)
            .where(
                MemoryConfigModel.workspace_id == workspace_id,
                MemoryConfigModel.is_default.is_(True),
                MemoryConfigModel.state.is_(True),
            )
            .limit(1)
        )
        
        config = self.db.scalars(stmt).first()
        
        if config:
            return config
        
        # Fallback: get the oldest active config if no explicit default
        stmt = (
            select(MemoryConfigModel)
            .where(
                MemoryConfigModel.workspace_id == workspace_id,
                MemoryConfigModel.state.is_(True),
            )
            .order_by(MemoryConfigModel.created_at.asc())
            .limit(1)
        )
        
        config = self.db.scalars(stmt).first()
        
        if not config:
            logger.warning(
                "No active memory config found for workspace fallback",
                extra={"workspace_id": str(workspace_id)}
            )
        
        return config

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
        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
        
        if not memory_config_id:
            logger.debug(
                "No memory config ID provided, using workspace default",
                extra={"workspace_id": str(workspace_id)}
            )
            return self._get_workspace_default_config(workspace_id)
        
        config = self.db.get(MemoryConfigModel, memory_config_id)
        
        if config:
            return config
        
        logger.warning(
            "Memory config not found, falling back to workspace default",
            extra={
                "missing_config_id": str(memory_config_id),
                "workspace_id": str(workspace_id)
            }
        )
        
        return self._get_workspace_default_config(workspace_id)

    def delete_config(
        self,
        config_id: UUID | int,
        force: bool = False
    ) -> dict:
        """Delete memory config with protection against in-use configs.
        
        Implements delete protection: prevents accidental deletion of configs
        that are actively being used by end users or marked as default.
        
        Args:
            config_id: Memory config ID to delete (UUID or legacy int)
            force: If True, delete even if end users are connected
            
        Returns:
            Dict with status, message, and affected_users count
            
        Raises:
            ResourceNotFoundException: If config doesn't exist
        """
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
        
        # TODO: add back delete warning
        # # Count connected end users
        # end_user_repo = EndUserRepository(self.db)
        # connected_count = end_user_repo.count_by_memory_config_id(config_id)
        
        # if connected_count > 0 and not force:
        #     logger.warning(
        #         "Attempted to delete memory config with connected end users",
        #         extra={
        #             "config_id": str(config_id),
        #             "connected_count": connected_count
        #         }
        #     )
            
        #     return {
        #         "status": "warning",
        #         "message": f"Cannot delete memory config: {connected_count} end users are using it",
        #         "connected_count": connected_count,
        #         "force_required": True
        #     }
        
        # # Force delete: clear end user references first
        # if connected_count > 0 and force:
        #     cleared_count = end_user_repo.clear_memory_config_id(config_id)
            
        #     logger.warning(
        #         "Force deleting memory config",
        #         extra={
        #             "config_id": str(config_id),
        #             "cleared_end_users": cleared_count
        #         }
        #     )
        connected_count = 0
        
        self.db.delete(config)
        self.db.commit()
        
        logger.info(
            "Memory config deleted",
            extra={
                "config_id": str(config_id),
                "force": force,
                "affected_users": connected_count
            }
        )
        
        return {
            "status": "success",
            "message": "Memory config deleted successfully",
            "affected_users": connected_count
        }
