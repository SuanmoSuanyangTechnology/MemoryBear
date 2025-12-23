# """
# 配置加载模块 - DEPRECATED

# ⚠️  DEPRECATION NOTICE ⚠️
# This module is deprecated and will be removed in a future version.
# Global configuration variables have been eliminated in favor of dependency injection.

# Use the new MemoryConfig system instead:
# - app.schemas.memory_config_schema.MemoryConfig for configuration objects
# - config_service = MemoryConfigService(db); config_service.load_memory_config(config_id)

# 阶段 1: 从 runtime.json 加载配置（路径 A）- DEPRECATED
# 阶段 2: 从数据库加载配置（路径 B，基于 dbrun.json 中的 config_id）- DEPRECATED  
# 阶段 3: 暴露配置常量供项目使用（路径 A 和 B 的汇合点）- DEPRECATED
# """
# import json
# import os
# import threading
# from datetime import datetime, timedelta
# from typing import Any, Dict, Optional

# #TODO: Fix this

# try:
#     from dotenv import load_dotenv
#     load_dotenv()
# except Exception:
#     pass

# # Import unified configuration system
# try:
#     from app.core.config import settings
#     USE_UNIFIED_CONFIG = True
# except ImportError:
#     USE_UNIFIED_CONFIG = False
#     settings = None

# # PROJECT_ROOT 应该指向 app/core/memory/ 目录
# # __file__ = app/core/memory/utils/config/definitions.py
# # os.path.dirname(__file__) = app/core/memory/utils/config
# # os.path.dirname(...) = app/core/memory/utils
# # os.path.dirname(...) = app/core/memory
# PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# # DEPRECATED: Global configuration lock removed
# # Use MemoryConfig objects with dependency injection instead

# # DEPRECATED: Legacy config.json loading removed
# # Use MemoryConfig objects with dependency injection instead
# CONFIG = {}

# DEFAULT_VALUES = {
#     "llm_name": "openai/qwen-plus",
#     "embedding_name": "openai/nomic-embed-text:v1.5",
#     "chunker_strategy": "RecursiveChunker",
#     "group_id": "group_123",
#     "user_id": "default_user",
#     "apply_id": "default_apply",
#     "llm_agent_name": "openai/qwen-plus",
#     "llm_verify_name": "openai/qwen-plus",
#     "llm_image_recognition": "openai/qwen-plus",
#     "llm_voice_recognition": "openai/qwen-plus",
#     "prompt_level": "DEBUG",
#     "reflexion_iteration_period": "3",
#     "reflexion_range": "retrieval",
#     "reflexion_baseline": "TIME",
# }

# # DEPRECATED: Legacy global variables for backward compatibility only
# # These will be removed in a future version
# # Use MemoryConfig objects with dependency injection instead
# # LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
# # SELECTED_LLM_ID = os.getenv("SELECTED_LLM_ID", DEFAULT_VALUES["llm_name"])


# # 阶段 1: 从 runtime.json 加载配置（路径 A）
# def _load_from_runtime_json() -> Dict[str, Any]:
#     """
#     DEPRECATED: Legacy runtime.json loading
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Use MemoryConfig objects with dependency injection instead.

#     Returns:
#         Dict[str, Any]: Empty configuration (legacy support only)
#     """
#     import warnings
#     warnings.warn(
#         "Runtime JSON loading is deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     return {"selections": {}}


# # 阶段 2: 从数据库加载配置（路径 B）- 已整合到统一加载器
# # 注意：此函数已被 _load_from_runtime_json 中的统一配置加载器替代
# # 保留此函数仅为向后兼容
# def _load_from_database() -> Optional[Dict[str, Any]]:
#     """
#     DEPRECATED: Legacy database configuration loading
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Use MemoryConfig objects with dependency injection instead.

#     Returns:
#         Optional[Dict[str, Any]]: None (deprecated functionality)
#     """
#     import warnings
#     warnings.warn(
#         "Database configuration loading is deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     return None


# # 阶段 3: 暴露配置常量（路径 A 和 B 的汇合点）- DEPRECATED
# def _expose_runtime_constants(runtime_cfg: Dict[str, Any]) -> None:
#     """
#     DEPRECATED: 将运行时配置暴露为全局常量供项目使用
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Global configuration variables have been eliminated in favor of dependency injection.
    
#     Use the new MemoryConfig system instead:
#     - app.core.memory_config.config.MemoryConfig for configuration objects
#     - Pass configuration objects as parameters instead of using global variables

#     Args:
#         runtime_cfg: 运行时配置字典
#     """
#     import warnings
#     warnings.warn(
#         "Global configuration variables are deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
    
#     # Keep minimal global state for backward compatibility only
#     # These will be removed in a future version
#     global RUNTIME_CONFIG, SELECTIONS
    
#     RUNTIME_CONFIG = runtime_cfg
#     SELECTIONS = RUNTIME_CONFIG.get("selections", {})
    
#     # All other global variables have been removed
#     # Use MemoryConfig objects instead


# # 初始化：使用统一配置加载器
# def _initialize_configuration() -> None:
#     """
#     DEPRECATED: Legacy configuration initialization
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Use MemoryConfig objects with dependency injection instead.
#     """
#     import warnings
#     warnings.warn(
#         "Global configuration initialization is deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     # Initialize with empty configuration for backward compatibility
#     _expose_runtime_constants({"selections": {}})


# # 模块加载时自动初始化配置
# _initialize_configuration()

# # DEPRECATED: Global variables removed
# # These variables have been eliminated in favor of dependency injection
# # Use MemoryConfig objects instead of accessing global variables


# # 公共 API：动态重新加载配置
# def reload_configuration_from_database(config_id, force_reload: bool = False) -> bool:
#     """
#     DEPRECATED: Legacy configuration reloading
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Use MemoryConfig objects with dependency injection instead.
    
#     For new code, use:
#     - app.services.memory_agent_service.MemoryAgentService.load_memory_config()
#     - app.services.memory_storage_service.MemoryStorageService.load_memory_config()

#     Args:
#         config_id: Configuration ID (deprecated)
#         force_reload: Force reload flag (deprecated)

#     Returns:
#         bool: Always returns False (deprecated functionality)
#     """
#     import logging
#     import warnings
    
#     logger = logging.getLogger(__name__)
    
#     warnings.warn(
#         "reload_configuration_from_database is deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
    
#     logger.warning(f"Deprecated function reload_configuration_from_database called with config_id={config_id}. "
#                   "Use MemoryConfig objects with dependency injection instead.")
    
#     return False





# def get_current_config_id() -> Optional[str]:
#     """
#     DEPRECATED: Legacy config ID retrieval
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Use MemoryConfig objects with dependency injection instead.
    
#     Returns:
#         Optional[str]: None (deprecated functionality)
#     """
#     import warnings
#     warnings.warn(
#         "get_current_config_id is deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
#     return None


# def ensure_fresh_config(config_id = None) -> bool:
#     """
#     DEPRECATED: Legacy configuration freshness check
    
#     ⚠️  This function is deprecated and will be removed in a future version.
#     Use MemoryConfig objects with dependency injection instead.
    
#     For new code, use:
#     - app.services.memory_agent_service.MemoryAgentService.load_memory_config()
#     - app.services.memory_storage_service.MemoryStorageService.load_memory_config()

#     Args:
#         config_id: Configuration ID (deprecated)
        
#     Returns:
#         bool: Always returns False (deprecated functionality)
#     """
#     import logging
#     import warnings
    
#     logger = logging.getLogger(__name__)
    
#     warnings.warn(
#         "ensure_fresh_config is deprecated. Use MemoryConfig objects with dependency injection instead.",
#         DeprecationWarning,
#         stacklevel=2
#     )
    
#     logger.warning(f"Deprecated function ensure_fresh_config called with config_id={config_id}. "
#                   "Use MemoryConfig objects with dependency injection instead.")
    
#     return False


