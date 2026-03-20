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
        from app.repositories.ontology_class_repository import OntologyClassRepository
        repo = OntologyClassRepository(db)
        classes = repo.get_classes_by_scene(scene_id)
        return [
            {"class_name": c.class_name, "class_description": c.class_description or ""}
            for c in classes if c.class_name
        ]
    except Exception as e:
        logger.warning(f"Failed to load ontology class infos for scene_id={scene_id}: {e}")
        return []


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
        config_id: Optional[UUID] = None,
        workspace_id: Optional[UUID] = None,
        service_name: str = "MemoryConfigService",
    ) -> MemoryConfig:
        """
        Load memory configuration from database with optional fallback.

        If config_id is provided, attempts to load that config directly.
        If config_id is None or not found and workspace_id is provided,
        falls back to the workspace's default configuration.

        Args:
            config_id: Configuration ID (UUID) from database (optional)
            workspace_id: Workspace ID for fallback lookup (optional)
            service_name: Name of the calling service (for logging purposes)

        Returns:
            MemoryConfig: Immutable configuration object

        Raises:
            ConfigurationError: If no valid configuration can be found
        """
        start_time = time.time()

        config_logger.info(
            "Starting memory configuration loading",
            extra={
                "operation": "load_memory_config",
                "service": service_name,
                "config_id": str(config_id) if config_id else None,
                "workspace_id": str(workspace_id) if workspace_id else None,
            },
        )

        logger.info(f"Loading memory configuration from database: config_id={config_id}, workspace_id={workspace_id}")

        try:
            # Use get_config_with_fallback if workspace_id is provided
            memory_config = None
            if workspace_id:
                validated_config_id = None
                if config_id:
                    try:
                        validated_config_id = _validate_config_id(config_id, self.db)
                    except Exception:
                        validated_config_id = None
                
                memory_config = self.get_config_with_fallback(
                    memory_config_id=validated_config_id,
                    workspace_id=workspace_id
                )
            elif config_id:
                validated_config_id = _validate_config_id(config_id, self.db)
                from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
                memory_config = self.db.get(MemoryConfigModel, validated_config_id)
            
            if not memory_config:
                elapsed_ms = (time.time() - start_time) * 1000
                config_logger.error(
                    "Configuration not found in database",
                    extra={
                        "operation": "load_memory_config",
                        "config_id": str(config_id) if config_id else None,
                        "workspace_id": str(workspace_id) if workspace_id else None,
                        "load_result": "not_found",
                        "elapsed_ms": elapsed_ms,
                        "service": service_name,
                    },
                )
                raise ConfigurationError(
                    f"Configuration not found: config_id={config_id}, workspace_id={workspace_id}"
                )

            # Get workspace for the config
            db_query_start = time.time()
            result = MemoryConfigRepository.get_config_with_workspace(self.db, memory_config.config_id)
            db_query_time = time.time() - db_query_start
            logger.info(f"[PERF] Config+Workspace query: {db_query_time:.4f}s")
            
            if not result:
                raise ConfigurationError(
                    f"Workspace not found for config {memory_config.config_id}"
                )

            memory_config, workspace = result

            # Helper function to validate model with workspace fallback
            def _validate_model_with_fallback(
                model_id: str,
                model_type: str,
                workspace_default: str,
                required: bool = False
            ) -> tuple:
                """Validate model ID, falling back to workspace default if invalid.
                
                Args:
                    model_id: The model ID to validate
                    model_type: Type of model (llm, embedding, rerank)
                    workspace_default: Workspace default model ID to use as fallback
                    required: Whether the model is required
                    
                Returns:
                    Tuple of (model_uuid, model_name) or (None, None)
                """
                # Try the configured model first
                if model_id:
                    try:
                        return validate_and_resolve_model_id(
                            model_id,
                            model_type,
                            self.db,
                            workspace.tenant_id,
                            required=False,
                            config_id=validated_config_id,
                            workspace_id=workspace.id,
                        )
                    except Exception as e:
                        logger.warning(
                            f"{model_type} model validation failed, trying workspace default: {e}"
                        )
                
                # Fallback to workspace default
                if workspace_default:
                    try:
                        result = validate_and_resolve_model_id(
                            workspace_default,
                            model_type,
                            self.db,
                            workspace.tenant_id,
                            required=required,
                            config_id=validated_config_id,
                            workspace_id=workspace.id,
                        )
                        if result[0]:
                            logger.info(
                                f"Using workspace default {model_type} model: {workspace_default}"
                            )
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
                        config_id=validated_config_id,
                        workspace_id=workspace.id
                    )
                
                return None, None

            # Step 2: Validate embedding model with workspace fallback
            embed_start = time.time()
            embedding_uuid, embedding_name = _validate_model_with_fallback(
                memory_config.embedding_id,
                "embedding",
                workspace.embedding,
                required=True
            )
            embed_time = time.time() - embed_start
            logger.info(f"[PERF] Embedding validation: {embed_time:.4f}s")

            # Step 3: Resolve LLM model with workspace fallback
            llm_start = time.time()
            llm_uuid, llm_name = _validate_model_with_fallback(
                memory_config.llm_id,
                "llm",
                workspace.llm,
                required=True
            )
            llm_time = time.time() - llm_start
            logger.info(f"[PERF] LLM validation: {llm_time:.4f}s")

            # Step 4: Resolve optional rerank model with workspace fallback
            rerank_start = time.time()
            rerank_uuid, rerank_name = _validate_model_with_fallback(
                memory_config.rerank_id,
                "rerank",
                workspace.rerank,
                required=False
            )
            rerank_time = time.time() - rerank_start
            if memory_config.rerank_id or workspace.rerank:
                logger.info(f"[PERF] Rerank validation: {rerank_time:.4f}s")

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
                # Ontology scene association
                scene_id=memory_config.scene_id,
                ontology_class_infos=_load_ontology_class_infos(self.db, memory_config.scene_id),
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
        config = MemoryConfigRepository.get_workspace_default(self.db, workspace_id)
        
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
        if not memory_config_id:
            logger.debug(
                "No memory config ID provided, using workspace default",
                extra={"workspace_id": str(workspace_id)}
            )
        
        config = MemoryConfigRepository.get_with_fallback(
            self.db,
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
        force: bool = False
    ) -> dict:
        """Delete memory config with protection against in-use configs.
        
        Implements delete protection: prevents accidental deletion of configs
        that are actively being used by end users or marked as default.
        
        Args:
            config_id: Memory config ID to delete (UUID or legacy int)
            force: If True, clear end user references before deleting
            
        Returns:
            Dict with status, message, and affected_users count
            
        Raises:
            ResourceNotFoundException: If config doesn't exist
        """
        from sqlalchemy.exc import IntegrityError

        from app.core.exceptions import ResourceNotFoundException
        from app.models.memory_config_model import MemoryConfig as MemoryConfigModel
        from app.repositories.end_user_repository import EndUserRepository
        
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
        
        # Use repository to count connected end users
        end_user_repo = EndUserRepository(self.db)
        connected_count = end_user_repo.count_by_memory_config_id(config_id)
        
        if connected_count > 0 and not force:
            logger.warning(
                "Attempted to delete memory config with connected end users",
                extra={
                    "config_id": str(config_id),
                    "connected_count": connected_count
                }
            )
            
            return {
                "status": "warning",
                "message": f"无法删除记忆配置：{connected_count} 个终端用户正在使用此配置",
                "connected_count": connected_count,
                "force_required": True
            }
        
        # Force delete: use repository to clear end user references first
        if connected_count > 0 and force:
            cleared_count = end_user_repo.clear_memory_config_id(config_id)
            
            logger.warning(
                "Force deleting memory config, clearing end user references",
                extra={
                    "config_id": str(config_id),
                    "cleared_end_users": cleared_count
                }
            )
        
        try:
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
                "message": "记忆配置删除成功",
                "affected_users": connected_count
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
        if app_type == "agent":
            return self._extract_memory_config_id_from_agent(config)
        elif app_type == "workflow":
            return self._extract_memory_config_id_from_workflow(config)
        elif app_type == "multi_agent":
            # Multi-agent 暂不支持记忆配置提取
            logger.debug(f"多智能体应用暂不支持记忆配置提取: app_type={app_type}")
            return None, False
        else:
            logger.warning(f"不支持的应用类型，无法提取记忆配置: app_type={app_type}")
            return None, False

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
            logger.info(
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
                        logger.warning(
                            f"Agent 配置中 memory_config_id 为旧格式 int 字符串，将使用工作空间默认配置: "
                            f"value={memory_value}"
                        )
                        return None, True
                    try:
                        return uuid.UUID(memory_value), False
                    except ValueError:
                        logger.warning(f"Invalid UUID string: {memory_value}")
                        return None, False
                elif isinstance(memory_value, int):
                    # 旧数据存储为 int，需要回退到工作空间默认配置
                    logger.warning(
                        f"Agent 配置中 memory_config_id 为旧格式 int，将使用工作空间默认配置: "
                        f"value={memory_value}"
                    )
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

    def _extract_memory_config_id_from_workflow(
        self,
        config: dict
    ) -> tuple[Optional[uuid.UUID], bool]:
        """从 Workflow 应用配置中提取 memory_config_id
        
        扫描工作流节点，查找 MemoryRead 或 MemoryWrite 节点。
        返回第一个找到的记忆节点的 config_id。
        
        Args:
            config: Workflow 配置字典
            
        Returns:
            Tuple[Optional[uuid.UUID], bool]: (memory_config_id, is_legacy_int)
                - memory_config_id: 记忆配置ID，如果不存在或为旧格式则返回 None
                - is_legacy_int: 是否检测到旧格式 int 数据
        """
        nodes = config.get("nodes", [])
        
        for node in nodes:
            node_type = node.get("type", "")
            
            # 检查是否为记忆节点 (support both formats: memory-read/memory-write and MemoryRead/MemoryWrite)
            if node_type.lower() in ["memoryread", "memorywrite", "memory-read", "memory-write"]:
                config_id = node.get("config", {}).get("config_id")
                
                if config_id:
                    try:
                        # 处理字符串、UUID 和 int（旧数据兼容）三种情况
                        if isinstance(config_id, uuid.UUID):
                            return config_id, False
                        elif isinstance(config_id, str):
                            return uuid.UUID(config_id), False
                        elif isinstance(config_id, int):
                            # 旧数据存储为 int，需要回退到工作空间默认配置
                            logger.warning(
                                f"工作流记忆节点 config_id 为旧格式 int，将使用工作空间默认配置: "
                                f"node_id={node.get('id')}, node_type={node_type}, value={config_id}"
                            )
                            return None, True
                        else:
                            logger.warning(
                                f"工作流记忆节点 config_id 格式无效: node_id={node.get('id')}, "
                                f"node_type={node_type}, type={type(config_id)}"
                            )
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            f"工作流记忆节点 config_id 格式无效: node_id={node.get('id')}, "
                            f"node_type={node_type}, error={str(e)}"
                        )
        
        logger.debug("工作流配置中未找到记忆节点")
        return None, False
