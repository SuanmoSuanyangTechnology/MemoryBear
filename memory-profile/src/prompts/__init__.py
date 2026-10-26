"""Prompt templates and their renderers.

Every prompt is a ``.jinja2`` file under :data:`TEMPLATES_DIR`; the named
renderers below are the supported way to load them, so prompt text never lives
inline in pipeline code.
"""

from __future__ import annotations

from src.prompts.loader import TEMPLATES_DIR, render_prompt, template_names
from src.prompts.renderers import (
    render_interest_filter_prompt,
    render_memory_insight_prompt,
    render_tag_filter_system_prompt,
    render_tag_filter_user_prompt,
    render_user_summary_prompt,
)

__all__ = [
    "TEMPLATES_DIR",
    "render_interest_filter_prompt",
    "render_memory_insight_prompt",
    "render_prompt",
    "render_tag_filter_system_prompt",
    "render_tag_filter_user_prompt",
    "render_user_summary_prompt",
    "template_names",
]
