import uuid
import json
from typing import Optional

from sqlalchemy.orm import Session
from fastapi.exceptions import HTTPException
from fastapi import status

from app.core.memory.utils.config.definitions import CONFIG, RUNTIME_CONFIG
from app.core.memory.models.variate_config import (
    ExtractionPipelineConfig,
    DedupConfig,
    StatementExtractionConfig,
    ForgettingEngineConfig,
)
from app.core.memory.models.config_models import PruningConfig
from app.db import get_db
from app.models.models_model import ModelConfig, ModelApiKey
from app.services.model_service import ModelConfigService
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


def get_pipeline_config() -> ExtractionPipelineConfig:
    """Build ExtractionPipelineConfig using only runtime.json values.

    Behavior:
    - Read `deduplication` section from runtime.json if present.
    - Read `statement_extraction` section from runtime.json if present.
    - Read `forgetting_engine` section from runtime.json if present.
    - If absent, check legacy top-level `enable_llm_dedup` key.
    - Do NOT fall back to environment variables.
    - Unspecified fields use model defaults defined in DedupConfig.
    """
    dedup_rc = RUNTIME_CONFIG.get("deduplication", {}) or {}
    stmt_rc = RUNTIME_CONFIG.get("statement_extraction", {}) or {}
    forget_rc = RUNTIME_CONFIG.get("forgetting_engine", {}) or {}

    # Assemble kwargs from runtime.json only
    kwargs = {}
    # LLM switch: prefer new key, then legacy top-level, default False
    if "enable_llm_dedup_blockwise" in dedup_rc:
        kwargs["enable_llm_dedup_blockwise"] = bool(dedup_rc.get("enable_llm_dedup_blockwise"))
    else:
        # Legacy top-level fallback inside runtime.json only
        legacy = RUNTIME_CONFIG.get("enable_llm_dedup")
        if legacy is not None:
            kwargs["enable_llm_dedup_blockwise"] = bool(legacy)
        else:
            kwargs["enable_llm_dedup_blockwise"] = False  # default reserve
    # Disambiguation switch: only from runtime.json deduplication section
    if "enable_llm_disambiguation" in dedup_rc:
        kwargs["enable_llm_disambiguation"] = bool(dedup_rc.get("enable_llm_disambiguation"))

    # Optional LLM fallback gating
    if "enable_llm_fallback_only_on_borderline" in dedup_rc:
        kwargs["enable_llm_fallback_only_on_borderline"] = bool(dedup_rc.get("enable_llm_fallback_only_on_borderline"))

    # Optional fuzzy thresholds: use values if provided; otherwise rely on DedupConfig defaults
    for key in (
        "fuzzy_name_threshold_strict",
        "fuzzy_type_threshold_strict",
        "fuzzy_overall_threshold",
        "fuzzy_unknown_type_name_threshold",
        "fuzzy_unknown_type_type_threshold",
    ):
        if key in dedup_rc:
            kwargs[key] = dedup_rc[key]

    # Optional weights and bonuses for overall scoring
    for key in (
        "name_weight",
        "desc_weight",
        "type_weight",
        "context_bonus",
        "llm_fallback_floor",
        "llm_fallback_ceiling",
    ):
        if key in dedup_rc:
            kwargs[key] = dedup_rc[key]

    # Optional LLM iterative dedup parameters
    for key in (
        "llm_block_size",
        "llm_block_concurrency",
        "llm_pair_concurrency",
        "llm_max_rounds",
    ):
        if key in dedup_rc:
            kwargs[key] = dedup_rc[key]

    dedup_config = DedupConfig(**kwargs)

    # Build StatementExtractionConfig from runtime.json
    stmt_kwargs = {}
    for key in (
        "statement_granularity",
        "temperature",
        "include_dialogue_context",
        "max_dialogue_context_chars",
    ):
        if key in stmt_rc:
            stmt_kwargs[key] = stmt_rc[key]
    stmt_config = StatementExtractionConfig(**stmt_kwargs)

    # Build ForgettingEngineConfig from runtime.json
    forget_kwargs = {}
    for key in ("offset", "lambda_time", "lambda_mem"):
        if key in forget_rc:
            forget_kwargs[key] = forget_rc[key]
    forget_config = ForgettingEngineConfig(**forget_kwargs)

    return ExtractionPipelineConfig(
        statement_extraction=stmt_config,
        deduplication=dedup_config,
        forgetting_engine=forget_config,
    )


def get_pruning_config() -> dict:
    """Retrieve semantic pruning config from runtime.json.

    Returns a dict suitable for PruningConfig.model_validate.

    Structure in runtime.json:
    {
      "pruning": {
        "enabled": true,
        "scene": "education" | "online_service" | "outbound",
        "threshold": 0.5
      }
    }
    """
    pruning_rc = RUNTIME_CONFIG.get("pruning", {}) or {}

    return {
        "pruning_switch": bool(pruning_rc.get("enabled", False)),
        "pruning_scene": pruning_rc.get("scene", "education"),
        "pruning_threshold": float(pruning_rc.get("threshold", 0.5)),
    }
