"""
Memory Configuration Service

Centralized configuration loading and management for memory services.
This service eliminates code duplication between MemoryAgentService and MemoryStorageService.
Database session management is handled internally.
"""

import time
from datetime import datetime

from app.core.logging_config import get_config_logger, get_logger
from app.core.validators.memory_config_validators import (
    validate_and_resolve_model_id,
    validate_embedding_model,
    validate_model_exists_and_active,
)
from app.repositories.data_config_repository import DataConfigRepository
from app.schemas.memory_config_schema import (
    ConfigurationError,
    InvalidConfigError,
    MemoryConfig,
    ModelInactiveError,
    ModelNotFoundError,
)
from sqlalchemy.orm import Session

logger = get_logger(__name__)
config_logger = get_config_logger()


def _validate_config_id(config_id):
    """Validate configuration ID format."""
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
        return config_id
    
    if isinstance(config_id, str):
        try:
            parsed_id = int(config_id.strip())
            if parsed_id <= 0:
                raise InvalidConfigError(
                    f"Configuration ID must be positive: {parsed_id}",
                    field_name="config_id",
                    invalid_value=config_id,
                )
            return parsed_id
        except ValueError as e:
            raise InvalidConfigError(
                f"Invalid configuration ID format: '{config_id}'",
                field_name="config_id",
                invalid_value=config_id,
            )
    
    raise InvalidConfigError(
        f"Invalid type for configuration ID: expected int or str, got {type(config_id).__name__}",
        field_name="config_id",
        invalid_value=config_id,
    )


class MemoryConfigService:
    """
    Centralized service for memory configuration loading and validation.
    
    This class provides a single implementation of configuration loading logic
    that can be shared across multiple services, eliminating code duplication.
    Database session management is handled internally.
    """
    
    @staticmethod
    def load_memory_config(
        config_id: int,
        service_name: str = "MemoryConfigService",
    ) -> MemoryConfig:
        """
        Load memory configuration from database by config_id.
        
        This method manages its own database session internally.
        
        Args:
            config_id: Configuration ID from database
            service_name: Name of the calling service (for logging purposes)
            
        Returns:
            MemoryConfig: Immutable configuration object
            
        Raises:
            ConfigurationError: If validation fails
        """
        from app.db import get_db
        
        db_gen = get_db()
        db = next(db_gen)
        
        try:
            return MemoryConfigService._load_memory_config_with_db(
                config_id=config_id,
                db=db,
                service_name=service_name,
            )
        finally:
            db.close()
    
    @staticmethod
    def _load_memory_config_with_db(
        config_id: int,
        db: Session,
        service_name: str = "MemoryConfigService",
    ) -> MemoryConfig:
        """Internal method that loads memory configuration with an existing db session."""
        start_time = time.time()
        
        config_logger.info(
            "Starting memory configuration loading",
            extra={
                "operation": "load_memory_config",
                "service": service_name,
                "config_id": config_id,
            },
        )
        
        logger.info(f"Loading memory configuration from database: config_id={config_id}")
        
        try:
            validated_config_id = _validate_config_id(config_id)
            
            result = DataConfigRepository.get_config_with_workspace(db, validated_config_id)
            if not result:
                elapsed_ms = (time.time() - start_time) * 1000
                config_logger.error(
                    "Configuration not found in database",
                    extra={
                        "operation": "load_memory_config",
                        "config_id": validated_config_id,
                        "load_result": "not_found",
                        "elapsed_ms": elapsed_ms,
                        "service": service_name,
                    },
                )
                raise ConfigurationError(
                    f"Configuration {validated_config_id} not found in database"
                )
            
            memory_config, workspace = result
            
            # Validate embedding model
            embedding_uuid = validate_embedding_model(
                validated_config_id,
                memory_config.embedding_id,
                db,
                workspace.tenant_id,
                workspace.id,
            )
            
            # Resolve LLM model
            llm_uuid, llm_name = validate_and_resolve_model_id(
                memory_config.llm_id,
                "llm",
                db,
                workspace.tenant_id,
                required=True,
                config_id=validated_config_id,
                workspace_id=workspace.id,
            )
            
            # Resolve optional rerank model
            rerank_uuid = None
            rerank_name = None
            if memory_config.rerank_id:
                rerank_uuid, rerank_name = validate_and_resolve_model_id(
                    memory_config.rerank_id,
                    "rerank",
                    db,
                    workspace.tenant_id,
                    required=False,
                    config_id=validated_config_id,
                    workspace_id=workspace.id,
                )
            
            # Get embedding model name
            embedding_name, _ = validate_model_exists_and_active(
                embedding_uuid,
                "embedding",
                db,
                workspace.tenant_id,
                config_id=validated_config_id,
                workspace_id=workspace.id,
            )
            
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
                reflexion_range=memory_config.reflexion_range or "retrieval",
                reflexion_baseline=memory_config.baseline or "time",
                loaded_at=datetime.now(),
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
