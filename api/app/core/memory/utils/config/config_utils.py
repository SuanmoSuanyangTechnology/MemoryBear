"""
Configuration utilities - Backward compatibility layer

For functions that don't require db (get_pipeline_config, get_pruning_config),
they are still re-exported here.
"""

import warnings

from app.services.memory_config_service import MemoryConfigService

# These functions don't require db - safe to re-export as static methods
get_pipeline_config = MemoryConfigService.get_pipeline_config
get_pruning_config = MemoryConfigService.get_pruning_config


def get_picture_config(llm_name: str) -> dict:
    """Retrieves the configuration for a specific model from the config file.
    
    .. deprecated::
        This function is deprecated and will be removed in a future version.
        Use database-backed model configuration instead.
    """
    warnings.warn(
        "get_picture_config is deprecated and will be removed in a future version. "
        "Use database-backed model configuration instead.",
        DeprecationWarning,
        stacklevel=2
    )
    for model_config in CONFIG.get("picture_recognition", []):
        if model_config["llm_name"] == llm_name:
            return model_config
    raise ValueError(f"Model '{llm_name}' not found in config.json")


def get_voice_config(llm_name: str) -> dict:
    """Retrieves the configuration for a specific model from the config file.
    
    .. deprecated::
        This function is deprecated and will be removed in a future version.
        Use database-backed model configuration instead.
    """
    warnings.warn(
        "get_voice_config is deprecated and will be removed in a future version. "
        "Use database-backed model configuration instead.",
        DeprecationWarning,
        stacklevel=2
    )
    for model_config in CONFIG.get("voice_recognition", []):
        if model_config["llm_name"] == llm_name:
            return model_config
    raise ValueError(f"Model '{llm_name}' not found in config.json")


def get_chunker_config(chunker_strategy: str) -> dict:
    """Retrieves the configuration for a specific chunker strategy."""

    default_configs = {
        "RecursiveChunker": {
            "chunker_strategy": "RecursiveChunker",
            "embedding_model": "BAAI/bge-m3",
            "chunk_size": 512,
            "min_characters_per_chunk": 50
        },
        "TokenChunker": {
            "chunker_strategy": "TokenChunker",
            "embedding_model": "BAAI/bge-m3",
            "chunk_size": 512,
        },
        "SentenceChunker": {
            "chunker_strategy": "SentenceChunker",
            "embedding_model": "BAAI/bge-m3",
            "chunk_size": 512,
        },
    }
    if chunker_strategy in default_configs:
        return default_configs[chunker_strategy]

    raise ValueError(
        f"Chunker '{chunker_strategy}' not found "
    )
