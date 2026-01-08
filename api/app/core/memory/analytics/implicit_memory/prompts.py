"""LLM Prompt Templates for Implicit Memory Analysis

This module contains prompt rendering functions for analyzing user memory summaries
to extract preferences, personality dimensions, interests, and behavioral habits.
"""

import os
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader

# Setup Jinja2 environment
current_dir = os.path.dirname(os.path.abspath(__file__))
prompt_dir = os.path.join(current_dir, "prompts")
prompt_env = Environment(loader=FileSystemLoader(prompt_dir))


def _render_template(template_name: str, **kwargs) -> str:
    """Helper function to render Jinja2 templates."""
    template = prompt_env.get_template(template_name)
    return template.render(**kwargs)


def get_preference_analysis_prompt(
    memory_summaries: List[Dict[str, Any]], 
    user_id: str
) -> str:
    """Get formatted preference analysis prompt using Jinja2 template."""
    return _render_template(
        "preference_analysis.jinja2",
        memory_summaries=memory_summaries,
        user_id=user_id
    )


def get_dimension_analysis_prompt(
    memory_summaries: List[Dict[str, Any]], 
    user_id: str
) -> str:
    """Get formatted dimension analysis prompt using Jinja2 template."""
    return _render_template(
        "dimension_analysis.jinja2",
        memory_summaries=memory_summaries,
        user_id=user_id
    )


def get_interest_analysis_prompt(
    memory_summaries: List[Dict[str, Any]], 
    user_id: str
) -> str:
    """Get formatted interest analysis prompt using Jinja2 template."""
    return _render_template(
        "interest_analysis.jinja2",
        memory_summaries=memory_summaries,
        user_id=user_id
    )


def get_habit_analysis_prompt(
    memory_summaries: List[Dict[str, Any]], 
    user_id: str
) -> str:
    """Get formatted habit analysis prompt using Jinja2 template."""
    return _render_template(
        "habit_analysis.jinja2",
        memory_summaries=memory_summaries,
        user_id=user_id
    )