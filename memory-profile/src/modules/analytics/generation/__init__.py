"""LLM generation pipelines for the profile analytics module.

Each pipeline takes its repositories and the resolved chat model as arguments;
model-configuration resolution lives in :mod:`src.modules.shared.model_config_service`
and persistence stays in the analytics service, so a pipeline can be exercised
without a database.
"""

from __future__ import annotations

from src.modules.analytics.generation.interest_distribution import (
    InterestDistributionGeneration,
    generate_interest_distribution,
)
from src.modules.analytics.generation.memory_insight import (
    MemoryInsightOutput,
    generate_memory_insight_output,
    refresh_memory_insight,
)
from src.modules.analytics.generation.user_summary import (
    generate_user_summary,
    parse_summary_sections,
)

__all__ = [
    "InterestDistributionGeneration",
    "MemoryInsightOutput",
    "generate_interest_distribution",
    "generate_memory_insight_output",
    "generate_user_summary",
    "parse_summary_sections",
    "refresh_memory_insight",
]
