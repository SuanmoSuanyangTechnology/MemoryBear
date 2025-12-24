"""Embedder Client Utilities

This module provides centralized functions for creating embedder clients.
"""

from typing import TYPE_CHECKING

from app.core.memory.llm_tools.openai_embedder import OpenAIEmbedderClient
from app.core.models.base import RedBearModelConfig
from app.db import get_db_context
from app.services.memory_config_service import MemoryConfigService

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig


def get_embedder_client_from_config(memory_config: "MemoryConfig") -> OpenAIEmbedderClient:
    """
    Get embedder client from MemoryConfig object.
    
    **PREFERRED METHOD**: Use this function in production code when you have a MemoryConfig object.
    This ensures proper configuration management and multi-tenant support.
    
    Args:
        memory_config: MemoryConfig object containing embedding_model_id
        
    Returns:
        OpenAIEmbedderClient: Initialized embedder client
        
    Raises:
        ValueError: If embedding model ID is not configured or client initialization fails
        
    Example:
        >>> embedder_client = get_embedder_client_from_config(memory_config)
    """
    if not memory_config.embedding_model_id:
        raise ValueError(
            f"Configuration {memory_config.config_id} has no embedding model configured"
        )
    return get_embedder_client(str(memory_config.embedding_model_id))


def get_embedder_client(embedding_id: str) -> OpenAIEmbedderClient:
    """
    Get embedder client by model ID.
    
    **LEGACY/TEST METHOD**: Use this function only for:
    - Test/evaluation code where you have a model ID directly
    - Legacy code that hasn't been migrated to MemoryConfig yet
    
    For production code with MemoryConfig, use get_embedder_client_from_config() instead.
    
    Args:
        embedding_id: Embedding model ID (required)
        
    Returns:
        OpenAIEmbedderClient: Initialized embedder client
        
    Raises:
        ValueError: If embedding_id is not provided or client initialization fails
        
    Example:
        >>> # For tests/evaluations only
        >>> embedder_client = get_embedder_client("model-uuid-string")
    """
    if not embedding_id:
        raise ValueError("Embedding ID is required but was not provided")

    try:
        with get_db_context() as db:
            embedder_config_dict = MemoryConfigService(db).get_embedder_config(embedding_id)
    except Exception as e:
        raise ValueError(f"Invalid embedding ID '{embedding_id}': {str(e)}") from e

    try:
        embedder_config = RedBearModelConfig(**embedder_config_dict)
        embedder_client = OpenAIEmbedderClient(embedder_config)
        return embedder_client
    except Exception as e:
        model_name = embedder_config_dict.get('model_name', 'unknown')
        raise ValueError(
            f"Failed to initialize embedder client for model '{model_name}': {str(e)}"
        ) from e
