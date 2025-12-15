"""
配置加载模块 - 三阶段架构（已迁移到统一配置管理）

本模块现在使用全局配置管理系统 (app/core/config.py)
来加载和管理配置，同时保持向后兼容性。

阶段 1: 从 runtime.json 加载配置（路径 A）
阶段 2: 从数据库加载配置（路径 B，基于 dbrun.json 中的 config_id）
阶段 3: 暴露配置常量供项目使用（路径 A 和 B 的汇合点）
"""
import os
import json
import threading
from typing import Any, Dict, Optional
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Import unified configuration system
try:
    from app.core.config import settings
    USE_UNIFIED_CONFIG = True
except ImportError:
    USE_UNIFIED_CONFIG = False
    settings = None

# PROJECT_ROOT 应该指向 app/core/memory/ 目录
# __file__ = app/core/memory/utils/config/definitions.py
# os.path.dirname(__file__) = app/core/memory/utils/config
# os.path.dirname(...) = app/core/memory/utils
# os.path.dirname(...) = app/core/memory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 全局配置锁 - 用于线程安全
_config_lock = threading.RLock()

# 加载基础配置（config.json）- 使用全局配置系统
if USE_UNIFIED_CONFIG:
    CONFIG = settings.load_memory_config()
else:
    # Fallback to legacy loading
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    try:
        with open(config_path, "r") as f:
            CONFIG = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Warning: config.json not found or is malformed. Using default settings.")
        CONFIG = {}

DEFAULT_VALUES = {
    "llm_name": "openai/qwen-plus",
    "embedding_name": "openai/nomic-embed-text:v1.5",
    "chunker_strategy": "RecursiveChunker",
    "group_id": "group_123",
    "user_id": "default_user",
    "apply_id": "default_apply",
    "llm_agent_name": "openai/qwen-plus",
    "llm_verify_name": "openai/qwen-plus",
    "llm_image_recognition": "openai/qwen-plus",
    "llm_voice_recognition": "openai/qwen-plus",
    "prompt_level": "DEBUG",
    "reflexion_iteration_period": "3",
    "reflexion_range": "retrieval",
    "reflexion_baseline": "TIME",
}


# 阶段 1: 从 runtime.json 加载配置（路径 A）
def _load_from_runtime_json() -> Dict[str, Any]:
    """
    从 runtime.json 文件加载配置（通过统一配置加载器）
    
    使用 overrides.py 的统一配置加载器，按优先级加载：
    1. 数据库配置（如果 dbrun.json 中有 config_id/group_id）
    2. 环境变量配置
    3. runtime.json 默认配置

    Returns:
        Dict[str, Any]: 运行时配置字典
    """
    try:
        # 使用 overrides.py 的统一配置加载器
        from app.core.memory.utils.config.overrides import load_unified_config
        
        runtime_cfg = load_unified_config(PROJECT_ROOT)
        return runtime_cfg
    except Exception as e:
        # Fallback: 直接读取 runtime.json
        runtime_config_path = os.path.join(PROJECT_ROOT, "runtime.json")
        try:
            with open(runtime_config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e2:
            pass  # print(f"[definitions] ❌ 无法加载 runtime.json: {e2}，使用空配置")
            return {"selections": {}}


# 阶段 2: 从数据库加载配置（路径 B）- 已整合到统一加载器
# 注意：此函数已被 _load_from_runtime_json 中的统一配置加载器替代
# 保留此函数仅为向后兼容
def _load_from_database() -> Optional[Dict[str, Any]]:
    """
    从数据库加载配置（基于 dbrun.json 中的 config_id）
    
    注意：此函数已被统一配置加载器替代，现在直接调用 _load_from_runtime_json
    即可获得包含数据库配置的完整配置。

    Returns:
        Optional[Dict[str, Any]]: 配置字典
    """
    try:
        # 直接使用统一配置加载器
        return _load_from_runtime_json()
    except Exception:
        return None


# 阶段 3: 暴露配置常量（路径 A 和 B 的汇合点）
def _expose_runtime_constants(runtime_cfg: Dict[str, Any]) -> None:
    """
    将运行时配置暴露为全局常量供项目使用

    这是路径 A（runtime.json）和路径 B（数据库）的汇合点，
    无论配置来自哪里，都通过这个函数统一暴露为常量。

    Args:
        runtime_cfg: 运行时配置字典
    """
    global RUNTIME_CONFIG, SELECTIONS, LOGGING_CONFIG
    global LANGFUSE_ENABLED, AGENTA_ENABLED, PROMPT_LOG_LEVEL_NAME
    global SELECTED_LLM_NAME, SELECTED_EMBEDDING_NAME, SELECTED_CHUNKER_STRATEGY
    global SELECTED_GROUP_ID, SELECTED_USER_ID, SELECTED_APPLY_ID, SELECTED_TEST_DATA_INDICES
    global SELECTED_LLM_AGENT_NAME, SELECTED_LLM_VERIFY_NAME, SELECTED_LLM_PICTURE_NAME, SELECTED_LLM_VOICE_NAME
    global SELECTED_LLM_ID, SELECTED_EMBEDDING_ID, SELECTED_RERANK_ID
    global REFLEXION_CONFIG, REFLEXION_ENABLED, REFLEXION_ITERATION_PERIOD, REFLEXION_RANGE, REFLEXION_BASELINE

    RUNTIME_CONFIG = runtime_cfg

    # 可观测性配置
    LANGFUSE_ENABLED = RUNTIME_CONFIG.get("langfuse", {}).get("enabled", False)
    AGENTA_ENABLED = RUNTIME_CONFIG.get("agenta", {}).get("enabled", False)

    # 日志配置
    LOGGING_CONFIG = RUNTIME_CONFIG.get("logging", {})
    PROMPT_LOG_LEVEL_NAME = LOGGING_CONFIG.get("prompt_level", DEFAULT_VALUES["prompt_level"])

    # 选择配置
    SELECTIONS = RUNTIME_CONFIG.get("selections", {})

    # 基础模型选择
    SELECTED_LLM_NAME = SELECTIONS.get("llm_name", DEFAULT_VALUES["llm_name"])
    SELECTED_EMBEDDING_NAME = SELECTIONS.get("embedding_name", DEFAULT_VALUES["embedding_name"])
    SELECTED_CHUNKER_STRATEGY = SELECTIONS.get("chunker_strategy", DEFAULT_VALUES["chunker_strategy"])

    # 分组和用户配置
    SELECTED_GROUP_ID = SELECTIONS.get("group_id", DEFAULT_VALUES["group_id"])
    SELECTED_USER_ID = SELECTIONS.get("user_id", DEFAULT_VALUES["user_id"])
    SELECTED_APPLY_ID = SELECTIONS.get("apply_id", DEFAULT_VALUES["apply_id"])
    SELECTED_TEST_DATA_INDICES = SELECTIONS.get("test_data_indices", None)

    # 专用 LLM 配置
    SELECTED_LLM_AGENT_NAME = SELECTIONS.get("llm_agent_name", DEFAULT_VALUES["llm_agent_name"])
    SELECTED_LLM_VERIFY_NAME = SELECTIONS.get("llm_verify_name", DEFAULT_VALUES["llm_verify_name"])
    SELECTED_LLM_PICTURE_NAME = SELECTIONS.get("llm_image_recognition", DEFAULT_VALUES["llm_image_recognition"])
    SELECTED_LLM_VOICE_NAME = SELECTIONS.get("llm_voice_recognition", DEFAULT_VALUES["llm_voice_recognition"])

    # 模型 ID 配置
    SELECTED_LLM_ID = SELECTIONS.get("llm_id", None)
    SELECTED_EMBEDDING_ID = SELECTIONS.get("embedding_id", None)
    SELECTED_RERANK_ID = SELECTIONS.get("rerank_id", None)
    
    # 反思配置
    REFLEXION_CONFIG = RUNTIME_CONFIG.get("reflexion", {})
    REFLEXION_ENABLED = REFLEXION_CONFIG.get("enabled", False)
    REFLEXION_ITERATION_PERIOD = REFLEXION_CONFIG.get("iteration_period", DEFAULT_VALUES["reflexion_iteration_period"])
    REFLEXION_RANGE = REFLEXION_CONFIG.get("reflexion_range", DEFAULT_VALUES["reflexion_range"])
    REFLEXION_BASELINE = REFLEXION_CONFIG.get("baseline", DEFAULT_VALUES["reflexion_baseline"])


# 初始化：使用统一配置加载器
def _initialize_configuration() -> None:
    """
    初始化配置：使用统一配置加载器
    
    配置加载优先级（由 overrides.py 统一处理）：
    1. 数据库配置（如果 dbrun.json 中有 config_id/group_id）
    2. 环境变量配置（.env）
    3. runtime.json 默认配置
    """
    try:
        
        # 使用统一配置加载器（已包含所有优先级处理）
        runtime_config = _load_from_runtime_json()
        
        # 暴露为全局常量
        _expose_runtime_constants(runtime_config)
        
   
    except Exception as e:
        pass  # print(f"[definitions] × 配置初始化失败: {e}")
        # 使用空配置
        _expose_runtime_constants({"selections": {}})


# 模块加载时自动初始化配置
_initialize_configuration()


# 公共 API：动态重新加载配置
def reload_configuration_from_database(config_id: int | str, force_reload: bool = False) -> bool:
    """
    动态重新加载配置（从数据库）- 使用统一配置加载器
    用于运行时切换配置，例如前端传入新的 config_id 时调用。

    注意：此函数仅在内存中覆写配置，不会修改 runtime.json 文件。

    Args:
        config_id: 配置 ID（整数或字符串，会自动转换）
        force_reload: 保留参数以保持向后兼容（已移除缓存逻辑）

    Returns:
        bool: 是否成功重新加载配置
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # 导入审计日志记录器
    try:
        from app.core.memory.utils.log.audit_logger import audit_logger
    except ImportError:
        audit_logger = None
    
    with _config_lock:
        try:
            from app.core.memory.utils.config.overrides import load_unified_config
        except Exception as e:
            logger.error(f"[definitions] 导入统一配置加载器失败: {e}")
            
            # 记录配置加载失败
            if audit_logger:
                audit_logger.log_config_load(
                    config_id=config_id,
                    success=False,
                    details={"error": f"Import failed: {str(e)}"}
                )
            
            return False

        try:
            logger.info(f"[definitions] 开始重新加载配置，config_id={config_id}")
            
            # 使用统一配置加载器（指定 config_id）
            updated_cfg = load_unified_config(PROJECT_ROOT, config_id=config_id)
            
            # 检查是否成功加载
            if not updated_cfg or not updated_cfg.get('selections'):
                logger.error(f"[definitions] 配置加载失败：数据库中未找到 config_id={config_id} 的配置")
                
                # 记录配置加载失败
                if audit_logger:
                    audit_logger.log_config_load(
                        config_id=config_id,
                        success=False,
                        details={"reason": "config not found in database"}
                    )
                
                return False

            # 重新暴露常量
            _expose_runtime_constants(updated_cfg)

            logger.info("[definitions] 配置重新加载成功，已暴露常量")
            logger.debug(f"[definitions] 配置详情: LLM_ID={updated_cfg.get('selections', {}).get('llm_id')}, "
                        f"EMBEDDING_ID={updated_cfg.get('selections', {}).get('embedding_id')}")

            # 记录成功的配置加载
            if audit_logger:
                selections = updated_cfg.get('selections', {})
                audit_logger.log_config_load(
                    config_id=config_id,
                    user_id=selections.get('user_id', None),
                    group_id=selections.get('group_id', None),
                    success=True,
                    details={
                        "llm_id": selections.get('llm_id'),
                        "embedding_id": selections.get('embedding_id'),
                        "chunker_strategy": selections.get('chunker_strategy')
                    }
                )

            return True
        except Exception as e:
            logger.error(f"[definitions] 重新加载配置时发生异常: {e}", exc_info=True)
            
            # 记录配置加载异常
            if audit_logger:
                audit_logger.log_config_load(
                    config_id=config_id,
                    success=False,
                    details={"error": str(e)}
                )
            
            return False





def get_current_config_id() -> Optional[str]:
    """
    获取当前使用的 config_id
    
    Returns:
        Optional[str]: 当前的 config_id，如果未设置则返回 None
    """
    return SELECTIONS.get("config_id", None)


def ensure_fresh_config(config_id: Optional[int | str] = None) -> bool:
    """
    确保使用最新的配置（每次写入操作前调用）
    
    如果提供了 config_id，则加载该配置；
    否则从 dbrun.json 读取并加载最新配置。
    
    Args:
        config_id: 可选的配置ID（整数或字符串，会自动转换）
        
    Returns:
        bool: 是否成功加载配置
    """
    import logging
    logger = logging.getLogger(__name__)
    
    with _config_lock:
        try:
            if config_id:
                # 使用指定的 config_id
                logger.debug(f"[definitions] 加载指定配置，config_id={config_id}")
                return reload_configuration_from_database(config_id)
            else:
                # 从数据库重新加载配置
                logger.debug("[definitions] 从数据库重新加载最新配置")
                memory_config = _load_from_database()
                
                if not memory_config or not memory_config.get('selections'):
                    logger.warning("[definitions] 未能从数据库加载配置，使用当前配置")
                    return False
                
                _expose_memory_constants(memory_config)
                return True
        except Exception as e:
            logger.error(f"[definitions] 加载配置失败: {e}", exc_info=True)
            return False


