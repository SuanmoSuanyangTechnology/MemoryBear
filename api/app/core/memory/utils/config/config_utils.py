from app.core.memory.models.variate_config import (
    DedupConfig,
    ExtractionPipelineConfig,
    ForgettingEngineConfig,
    StatementExtractionConfig,
)
from app.core.memory.utils.config.definitions import CONFIG
from app.db import get_db
from app.models.models_model import ModelApiKey
from app.services.model_service import ModelConfigService
from fastapi import status
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session


def get_model_config(model_id: str, db: Session | None = None) -> dict:
    if db is None:
        db_gen = get_db()             # get_db 通常是一个生成器
        db = next(db_gen)             # 取到真正的 Session

    config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    if not config:
        print(f"模型ID {model_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型ID不存在")
    apiConfig: ModelApiKey = config.api_keys[0]
    
    # 从环境变量读取超时和重试配置
    from app.core.config import settings
    
    model_config = {
        "model_name": apiConfig.model_name,
        "provider": apiConfig.provider,
        "api_key": apiConfig.api_key,
        "base_url": apiConfig.api_base,
        "model_config_id":apiConfig.model_config_id,
        "type": config.type,
        # 添加超时和重试配置，避免 LLM 请求超时
        "timeout": settings.LLM_TIMEOUT,  # 从环境变量读取，默认120秒
        "max_retries": settings.LLM_MAX_RETRIES,  # 从环境变量读取，默认2次
    }
    # 写入model_config.log文件中
    with open("logs/model_config.log", "a", encoding="utf-8") as f:
        f.write(f"模型ID: {model_id}\n")
        f.write(f"模型配置信息:\n{model_config}\n")
        f.write("=============================\n\n")
    return model_config

def get_embedder_config(embedding_id: str, db: Session | None = None) -> dict:
    if db is None:
        db_gen = get_db()             # get_db 通常是一个生成器
        db = next(db_gen)             # 取到真正的 Session

    config = ModelConfigService.get_model_by_id(db=db, model_id=embedding_id)
    if not config:
        print(f"嵌入模型ID {embedding_id} 不存在")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="嵌入模型ID不存在")
    apiConfig: ModelApiKey = config.api_keys[0]
    model_config = {
        "model_name": apiConfig.model_name,
        "provider": apiConfig.provider,
        "api_key": apiConfig.api_key,
        "base_url": apiConfig.api_base,
        "model_config_id":apiConfig.model_config_id,
        # Ensure required field for RedBearModelConfig validation
        "type": config.type,
        # 添加超时和重试配置，避免嵌入服务请求超时
        "timeout": 120.0,  # 嵌入服务超时时间（秒）
        "max_retries": 5,  # 最大重试次数
    }
    # 写入embedder_config.log文件中
    with open("logs/embedder_config.log", "a", encoding="utf-8") as f:
        f.write(f"嵌入模型ID: {embedding_id}\n")
        f.write(f"嵌入模型配置信息:\n{model_config}\n")
        f.write("=============================\n\n")
    return model_config

def get_neo4j_config() -> dict:
    """Retrieves the Neo4j configuration from the config file."""
    return CONFIG.get("neo4j", {})
def get_picture_config(llm_name: str) -> dict:
    """Retrieves the configuration for a specific model from the config file."""
    for model_config in CONFIG.get("picture_recognition", []):
        if model_config["llm_name"] == llm_name:
            return model_config
    raise ValueError(f"Model '{llm_name}' not found in config.json")
def get_voice_config(llm_name: str) -> dict:
    """Retrieves the configuration for a specific model from the config file."""
    for model_config in CONFIG.get("voice_recognition", []):
        if model_config["llm_name"] == llm_name:
            return model_config
    raise ValueError(f"Model '{llm_name}' not found in config.json")


def get_chunker_config(chunker_strategy: str) -> dict:
    """Retrieves the configuration for a specific chunker strategy.

    Enhancements:
    - Supports default configs for `LLMChunker` and `HybridChunker` if not present.
    - Falls back to the first available chunker config when the requested one is missing.
    """
    # 1) Try to find exact match in config
    chunker_list = CONFIG.get("chunker_list", [])
    for chunker_config in chunker_list:
        if chunker_config.get("chunker_strategy") == chunker_strategy:
            return chunker_config

    # 2) Provide sane defaults for newer strategies
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

    # 3) Fallback: use first available config but tag with requested strategy
    if chunker_list:
        fallback = chunker_list[0].copy()
        fallback["chunker_strategy"] = chunker_strategy
        # Non-fatal notice for visibility in logs if any
        print(f"Warning: Using first available chunker config as fallback for '{chunker_strategy}'")
        return fallback

    # 4) If no configs available at all
    raise ValueError(
        f"Chunker '{chunker_strategy}' not found in config.json and no default or fallback available"
    )

#TODO: Fix this

def get_pipeline_config(
    config_id: int,
    db: Session | None = None,
) -> ExtractionPipelineConfig:
    """Build ExtractionPipelineConfig from database.

    Args:
        config_id: Database configuration ID (required). Loads pipeline
            settings from the DataConfig table.
        db: Optional database session. If not provided, a new session
            will be created.

    Returns:
        ExtractionPipelineConfig with deduplication, statement extraction,
        and forgetting engine settings loaded from database.

    Raises:
        ValueError: If config_id not found in database.
    """
    from app.repositories.data_config_repository import DataConfigRepository

    # Load from database
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
    
    db_config = DataConfigRepository.get_by_id(db, config_id)
    if db_config is None:
        raise ValueError(f"Configuration {config_id} not found in database")

    # Build DedupConfig from database
    dedup_kwargs = {
        "enable_llm_dedup_blockwise": bool(db_config.enable_llm_dedup_blockwise) if db_config.enable_llm_dedup_blockwise is not None else False,
        "enable_llm_disambiguation": bool(db_config.enable_llm_disambiguation) if db_config.enable_llm_disambiguation is not None else False,
    }
    
    # Fuzzy thresholds
    if db_config.t_name_strict is not None:
        dedup_kwargs["fuzzy_name_threshold_strict"] = db_config.t_name_strict
    if db_config.t_type_strict is not None:
        dedup_kwargs["fuzzy_type_threshold_strict"] = db_config.t_type_strict
    if db_config.t_overall is not None:
        dedup_kwargs["fuzzy_overall_threshold"] = db_config.t_overall

    dedup_config = DedupConfig(**dedup_kwargs)

    # Build StatementExtractionConfig from database
    stmt_kwargs = {}
    if db_config.statement_granularity is not None:
        stmt_kwargs["statement_granularity"] = db_config.statement_granularity
    if db_config.include_dialogue_context is not None:
        stmt_kwargs["include_dialogue_context"] = bool(db_config.include_dialogue_context)
    if db_config.max_context is not None:
        stmt_kwargs["max_dialogue_context_chars"] = db_config.max_context

    stmt_config = StatementExtractionConfig(**stmt_kwargs)

    # Build ForgettingEngineConfig from database
    forget_kwargs = {}
    if db_config.offset is not None:
        forget_kwargs["offset"] = db_config.offset
    if db_config.lambda_time is not None:
        forget_kwargs["lambda_time"] = db_config.lambda_time
    if db_config.lambda_mem is not None:
        forget_kwargs["lambda_mem"] = db_config.lambda_mem

    forget_config = ForgettingEngineConfig(**forget_kwargs)

    return ExtractionPipelineConfig(
        statement_extraction=stmt_config,
        deduplication=dedup_config,
        forgetting_engine=forget_config,
    )


def get_pruning_config(
    config_id: int,
    db: Session | None = None,
) -> dict:
    """Retrieve semantic pruning config from database.

    Args:
        config_id: Database configuration ID (required).
        db: Optional database session.

    Returns:
        Dict suitable for PruningConfig.model_validate with keys:
        - pruning_switch: bool
        - pruning_scene: str ("education" | "online_service" | "outbound")
        - pruning_threshold: float (0-0.9)

    Raises:
        ValueError: If config_id not found in database.
    """
    from app.repositories.data_config_repository import DataConfigRepository

    if db is None:
        db_gen = get_db()
        db = next(db_gen)

    db_config = DataConfigRepository.get_by_id(db, config_id)
    if db_config is None:
        raise ValueError(f"Configuration {config_id} not found in database")

    return {
        "pruning_switch": bool(db_config.pruning_enabled) if db_config.pruning_enabled is not None else False,
        "pruning_scene": db_config.pruning_scene or "education",
        "pruning_threshold": float(db_config.pruning_threshold) if db_config.pruning_threshold is not None else 0.5,
    }
