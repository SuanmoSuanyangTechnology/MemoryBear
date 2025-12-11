"""
Memory 模块工具函数包

本包包含 Memory 模块使用的所有工具函数，按功能分类管理。

目录结构：
- config/: 配置管理模块（config_utils, definitions, overrides, get_data, litellm_config, config_optimization）
- log/: 日志管理模块（logging_utils, audit_logger）
- prompt/: 提示词管理模块（prompt_utils, template_render, prompts/）
- llm/: LLM 工具模块（llm_utils）
- data/: 数据处理模块（text_utils, time_utils, ontology）
- paths/: 路径管理模块（output_paths）
- visualization/: 可视化模块（forgetting_visualizer）
- self_reflexion_utils/: 自我反思工具（evaluate, reflexion, self_reflexion）

注意：
- json_schema 和 messages 已迁移到 app.schemas.memory_storage_schema
- 所有工具函数已按功能分类到对应的子目录

使用示例：
    # 配置管理
    from app.core.memory.utils.config import get_model_config
    from app.core.memory.utils.config.definitions import SELECTED_LLM_ID
    
    # 日志管理
    from app.core.memory.utils.log import log_prompt_rendering, audit_logger
    
    # 提示词管理
    from app.core.memory.utils.prompt import render_statement_extraction_prompt
    
    # LLM 工具
    from app.core.memory.utils.llm import get_llm_client
    
    # 数据处理
    from app.core.memory.utils.data import text_utils, time_utils
    from app.core.memory.utils.data.ontology import Predicate, StatementType
    
    # 路径管理
    from app.core.memory.utils.paths import get_output_dir
    
    # 可视化
    from app.core.memory.utils.visualization import visualize_forgetting_curve
    
    # 自我反思
    from app.core.memory.utils.self_reflexion_utils import self_reflexion
"""

# 不在 __init__.py 中进行模块级别的导入，以避免循环导入
# 用户应该直接导入需要的模块，例如：
# from app.core.memory.utils.config import config_utils
# from app.core.memory.utils.log import logging_utils
# from app.core.memory.utils.data import text_utils
# from app.core.memory.utils.prompt import prompt_utils

__all__ = [
    # 子模块
    "config",
    "log",
    "prompt",
    "llm",
    "data",
    "paths",
    "visualization",
    "self_reflexion_utils",
]
