"""The single home for generation prompts.

Templates live under ``templates/`` and each feature imports its renderer from
here instead of carrying a private prompt module, so the prompt set can be
audited (and diffed against the API's copies) in one place.
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
