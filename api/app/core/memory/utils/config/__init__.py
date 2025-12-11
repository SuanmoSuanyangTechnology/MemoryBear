"""
配置管理模块

包含所有配置相关的工具函数和定义。
"""

# 从子模块导出常用函数和常量，保持向后兼容
from .config_utils import (
    get_model_config,
    get_embedder_config,
    get_neo4j_config,
    get_chunker_config,
    get_pipeline_config,
    get_pruning_config,
    get_picture_config,
    get_voice_config,
)
from .definitions import (
    CONFIG,
    RUNTIME_CONFIG,
    PROJECT_ROOT,
    SELECTED_LLM_ID,
    SELECTED_EMBEDDING_ID,
    SELECTED_GROUP_ID,
    SELECTED_RERANK_ID,
    SELECTED_LLM_PICTURE_NAME,
    SELECTED_LLM_VOICE_NAME,
    REFLEXION_ENABLED,
    REFLEXION_ITERATION_PERIOD,
    REFLEXION_RANGE,
    REFLEXION_BASELINE,
    reload_configuration_from_database,
)
from .overrides import load_unified_config
from .get_data import get_data
# litellm_config 需要时动态导入，避免循环依赖
# from .litellm_config import (
#     LiteLLMConfig,
#     setup_litellm_enhanced,
#     get_usage_summary,
#     print_usage_summary,
#     get_instant_qps,
#     print_instant_qps,
# )

__all__ = [
    # config_utils
    "get_model_config",
    "get_embedder_config",
    "get_neo4j_config",
    "get_chunker_config",
    "get_pipeline_config",
    "get_pruning_config",
    "get_picture_config",
    "get_voice_config",
    # definitions
    "CONFIG",
    "RUNTIME_CONFIG",
    "PROJECT_ROOT",
    "SELECTED_LLM_ID",
    "SELECTED_EMBEDDING_ID",
    "SELECTED_GROUP_ID",
    "SELECTED_RERANK_ID",
    "SELECTED_LLM_PICTURE_NAME",
    "SELECTED_LLM_VOICE_NAME",
    "REFLEXION_ENABLED",
    "REFLEXION_ITERATION_PERIOD",
    "REFLEXION_RANGE",
    "REFLEXION_BASELINE",
    "reload_configuration_from_database",
    # overrides
    "load_unified_config",
    # get_data
    "get_data",
    # litellm_config - 需要时从 .litellm_config 直接导入
    # "LiteLLMConfig",
    # "setup_litellm_enhanced",
    # "get_usage_summary",
    # "print_usage_summary",
    # "get_instant_qps",
    # "print_instant_qps",
]
