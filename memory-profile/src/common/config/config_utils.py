"""Active extraction configuration helpers and backward-compatible exports."""

from src.services.memory_config.service import get_pipeline_config, get_pruning_config


def get_chunker_config(chunker_strategy: str) -> dict:
    """Retrieves the configuration for a specific chunker strategy."""

    default_configs = {
        "RecursiveChunker": {
            "chunker_strategy": "RecursiveChunker",
            "embedding_model": "BAAI/bge-m3",
            "chunk_size": 512,
            "min_characters_per_chunk": 50,
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

    raise ValueError(f"Chunker '{chunker_strategy}' not found ")
