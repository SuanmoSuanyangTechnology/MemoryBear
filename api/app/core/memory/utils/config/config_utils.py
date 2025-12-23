"""
Configuration utilities - Backward compatibility layer

DEPRECATED: These functions now require a db session parameter.
New code should use MemoryConfigService(db) instance directly.

For functions that don't require db (get_pipeline_config, get_pruning_config),
they are still re-exported here.
"""

import warnings

from app.services.memory_config_service import MemoryConfigService

# These functions don't require db - safe to re-export as static methods
get_pipeline_config = MemoryConfigService.get_pipeline_config
get_pruning_config = MemoryConfigService.get_pruning_config


def get_model_config(model_id: str, db=None):
    """DEPRECATED: Use MemoryConfigService(db).get_model_config(model_id) directly."""
    if db is None:
        raise ValueError(
            "get_model_config now requires a db session. "
            "Use MemoryConfigService(db).get_model_config(model_id) directly."
        )
    return MemoryConfigService(db).get_model_config(model_id)


def get_embedder_config(embedding_id: str, db=None):
    """DEPRECATED: Use MemoryConfigService(db).get_embedder_config(embedding_id) directly."""
    if db is None:
        raise ValueError(
            "get_embedder_config now requires a db session. "
            "Use MemoryConfigService(db).get_embedder_config(embedding_id) directly."
        )
    return MemoryConfigService(db).get_embedder_config(embedding_id)


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
        "LLMChunker": {
            "chunker_strategy": "LLMChunker",
            "embedding_model": "BAAI/bge-m3",
            "chunk_size": 1000,
            "threshold": 0.8,
            "min_sentences": 2,
            "language": "zh",
            "skip_window": 1,
            "min_characters_per_chunk": 100,
        },
        "HybridChunker": {
            "chunker_strategy": "HybridChunker",
            "embedding_model": "BAAI/bge-m3",
            "chunk_size": 512,
            "threshold": 0.8,
            "min_sentences": 2,
            "language": "zh",
            "skip_window": 1,
            "min_characters_per_chunk": 100,
        },
    }
    if chunker_strategy in default_configs:
        return default_configs[chunker_strategy]

    raise ValueError(
        f"Chunker '{chunker_strategy}' not found "
    )
