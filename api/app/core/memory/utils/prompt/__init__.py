"""
提示词管理模块

包含所有提示词渲染和模板管理相关的工具函数。
"""

# 从子模块导出常用函数，保持向后兼容
from .prompt_utils import (
    get_prompts,
    render_statement_extraction_prompt,
    render_temporal_extraction_prompt,
    render_entity_dedup_prompt,
    render_triplet_extraction_prompt,
    render_memory_summary_prompt,
    prompt_env,
)
from .template_render import (
    render_evaluate_prompt,
    render_reflexion_prompt,
)

__all__ = [
    # prompt_utils
    "get_prompts",
    "render_statement_extraction_prompt",
    "render_temporal_extraction_prompt",
    "render_entity_dedup_prompt",
    "render_triplet_extraction_prompt",
    "render_memory_summary_prompt",
    "prompt_env",
    # template_render
    "render_evaluate_prompt",
    "render_reflexion_prompt",
]
