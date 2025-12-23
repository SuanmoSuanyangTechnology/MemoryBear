"""
配置管理模块

包含所有配置相关的工具函数和定义。
"""

# 从子模块导出常用函数和常量，保持向后兼容
from .config_utils import (
    get_chunker_config,
    get_embedder_config,
    get_model_config,
    get_picture_config,
    get_pipeline_config,
    get_pruning_config,
    get_voice_config,
)

# DEPRECATED: Global configuration variables removed
# Use MemoryConfig objects with dependency injection instead
# from .definitions import (
#     CONFIG,  # DEPRECATED - empty dict for backward compatibility
#     RUNTIME_CONFIG,  # DEPRECATED - minimal for backward compatibility  
#     PROJECT_ROOT,  # Still needed for file paths
#     reload_configuration_from_database,  # DEPRECATED - returns False
# )
# DEPRECATED: overrides module removed - use MemoryConfig with dependency injection
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
    "get_chunker_config",
    "get_pipeline_config",
    "get_pruning_config",
    "get_picture_config",
    "get_voice_config",
    # definitions (DEPRECATED - use MemoryConfig objects instead)
    # "CONFIG",  # DEPRECATED
    # "RUNTIME_CONFIG",  # DEPRECATED
    # "PROJECT_ROOT",
    # "reload_configuration_from_database",  # DEPRECATED
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
