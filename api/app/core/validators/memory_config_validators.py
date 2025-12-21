# -*- coding: utf-8 -*-
"""Memory Configuration Validators

This module provides validation functions for memory configuration models.

Functions:
    validate_model_exists_and_active: Validate model exists and is active
    validate_and_resolve_model_id: Validate and resolve model ID with DB lookup
    validate_embedding_model: Validate embedding model availability
    validate_llm_model: Validate LLM model availability
"""

import time
from typing import Optional, Union
from uuid import UUID

from app.core.logging_config import get_config_logger
from app.schemas.memory_config_schema import (
    InvalidConfigError,
    ModelInactiveError,
    ModelNotFoundError,
)
from sqlalchemy.orm import Session

logger = get_config_logger()


def _parse_model_id(model_id: Union[str, UUID, None], model_type: str,
                    config_id: Optional[int] = None, workspace_id: Optional[UUID] = None) -> Optional[UUID]:
    """Parse model ID from string or UUID."""
    if model_id is None:
        return None
    if isinstance(model_id, UUID):
        return model_id
    if isinstance(model_id, str):
        if not model_id.strip():
            return None
        try:
            return UUID(model_id.strip())
        except ValueError:
            raise InvalidConfigError(
                f"Invalid UUID format for {model_type} model ID: '{model_id}'",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id,
                config_id=config_id,
                workspace_id=workspace_id
            )
    raise InvalidConfigError(
        f"Invalid type for {model_type} model ID: expected str or UUID, got {type(model_id).__name__}",
        field_name=f"{model_type}_model_id",
        invalid_value=model_id,
        config_id=config_id,
        workspace_id=workspace_id
    )


def validate_model_exists_and_active(
    model_id: UUID,
    model_type: str,
    db: Session,
    tenant_id: Optional[UUID] = None,
    config_id: Optional[int] = None,
    workspace_id: Optional[UUID] = None
) -> tuple[str, bool]:
    """Validate that a model exists and is active.
    
    Args:
        model_id: Model UUID to validate
        model_type: Type of model ("llm", "embedding", "rerank")
        db: Database session
        tenant_id: Optional tenant ID for filtering
        config_id: Optional configuration ID for error context
        workspace_id: Optional workspace ID for error context
        
    Returns:
        Tuple of (model_name, is_active)
        
    Raises:
        ModelNotFoundError: If model does not exist
        ModelInactiveError: If model exists but is inactive
    """
    from app.repositories.model_repository import ModelConfigRepository
    
    start_time = time.time()
    
    try:
        model = ModelConfigRepository.get_by_id(db, model_id, tenant_id)
        elapsed_ms = (time.time() - start_time) * 1000
        
        if not model:
            logger.warning(
                "Model not found",
                extra={"model_id": str(model_id), "model_type": model_type, "elapsed_ms": elapsed_ms}
            )
            raise ModelNotFoundError(
                model_id=model_id,
                model_type=model_type,
                config_id=config_id,
                workspace_id=workspace_id,
                message=f"{model_type.title()} model {model_id} not found"
            )
        
        if not model.is_active:
            logger.warning(
                "Model inactive",
                extra={"model_id": str(model_id), "model_name": model.name, "elapsed_ms": elapsed_ms}
            )
            raise ModelInactiveError(
                model_id=model_id,
                model_name=model.name,
                model_type=model_type,
                config_id=config_id,
                workspace_id=workspace_id,
                message=f"{model_type.title()} model {model_id} ({model.name}) is inactive"
            )
        
        logger.debug(
            "Model validation successful",
            extra={"model_id": str(model_id), "model_name": model.name, "elapsed_ms": elapsed_ms}
        )
        return model.name, model.is_active
        
    except (ModelNotFoundError, ModelInactiveError):
        raise
    except Exception as e:
        logger.error(f"Model validation failed: {e}", exc_info=True)
        raise


def validate_and_resolve_model_id(
    model_id_str: Union[str, UUID, None],
    model_type: str,
    db: Session,
    tenant_id: Optional[UUID] = None,
    required: bool = False,
    config_id: Optional[int] = None,
    workspace_id: Optional[UUID] = None
) -> tuple[Optional[UUID], Optional[str]]:
    """Validate and resolve a model ID, checking existence and active status.
    
    Returns:
        Tuple of (validated_uuid, model_name) or (None, None) if not required and empty
    """
    if model_id_str is None or (isinstance(model_id_str, str) and not model_id_str.strip()):
        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model ID is required",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id_str,
                config_id=config_id,
                workspace_id=workspace_id
            )
        return None, None
    
    model_uuid = _parse_model_id(model_id_str, model_type, config_id, workspace_id)
    if model_uuid is None:
        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model ID is required",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id_str,
                config_id=config_id,
                workspace_id=workspace_id
            )
        return None, None
    
    model_name, _ = validate_model_exists_and_active(
        model_uuid, model_type, db, tenant_id, config_id, workspace_id
    )
    return model_uuid, model_name


def validate_embedding_model(
    config_id: int,
    embedding_id: Union[str, UUID, None],
    db: Session,
    tenant_id: Optional[UUID] = None,
    workspace_id: Optional[UUID] = None
) -> UUID:
    """Validate that embedding model is available and return its UUID.
    
    Raises:
        InvalidConfigError: If embedding_id is not provided or invalid
        ModelNotFoundError: If embedding model does not exist
        ModelInactiveError: If embedding model is inactive
    """
    if embedding_id is None or (isinstance(embedding_id, str) and not embedding_id.strip()):
        raise InvalidConfigError(
            f"Configuration {config_id} has no embedding model configured",
            field_name="embedding_model_id",
            invalid_value=embedding_id,
            config_id=config_id,
            workspace_id=workspace_id
        )
    
    embedding_uuid, _ = validate_and_resolve_model_id(
        embedding_id, "embedding", db, tenant_id, required=True,
        config_id=config_id, workspace_id=workspace_id
    )
    
    if embedding_uuid is None:
        raise InvalidConfigError(
            f"Configuration {config_id} has no embedding model configured",
            field_name="embedding_model_id",
            invalid_value=embedding_id,
            config_id=config_id,
            workspace_id=workspace_id
        )
    
    return embedding_uuid


def validate_llm_model(
    config_id: int,
    llm_id: Union[str, UUID, None],
    db: Session,
    tenant_id: Optional[UUID] = None,
    workspace_id: Optional[UUID] = None
) -> UUID:
    """Validate that LLM model is available and return its UUID.
    
    Raises:
        InvalidConfigError: If llm_id is not provided or invalid
        ModelNotFoundError: If LLM model does not exist
        ModelInactiveError: If LLM model is inactive
    """
    if llm_id is None or (isinstance(llm_id, str) and not llm_id.strip()):
        raise InvalidConfigError(
            f"Configuration {config_id} has no LLM model configured",
            field_name="llm_model_id",
            invalid_value=llm_id,
            config_id=config_id,
            workspace_id=workspace_id
        )
    
    llm_uuid, _ = validate_and_resolve_model_id(
        llm_id, "llm", db, tenant_id, required=True,
        config_id=config_id, workspace_id=workspace_id
    )
    
    if llm_uuid is None:
        raise InvalidConfigError(
            f"Configuration {config_id} has no LLM model configured",
            field_name="llm_model_id",
            invalid_value=llm_id,
            config_id=config_id,
            workspace_id=workspace_id
        )
    
    return llm_uuid
