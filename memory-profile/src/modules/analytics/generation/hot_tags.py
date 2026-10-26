"""LLM-refined entity tags used as user-summary input.

Ported from the API's ``hot_memory_tags.get_hot_memory_tags``: raw entity counts
come from Neo4j, then one structured LLM call keeps only representative nouns.
The API falls back to the unfiltered list when the LLM is unavailable; that
fallback is preserved so a provider failure degrades instead of failing.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from src.infrastructure.model_service import ProfileChatModel
from src.modules.analytics.repository.generation_graph import GenerationGraphRepository
from src.prompts import (
    render_tag_filter_system_prompt,
    render_tag_filter_user_prompt,
)

logger = logging.getLogger(__name__)

#: 查询更多候选给 LLM（api ``get_hot_memory_tags`` 同值）。
TAG_QUERY_LIMIT = 40


class FilteredTags(BaseModel):
    """LLM 筛选后的核心标签列表。"""

    meaningful_tags: list[str] = Field(
        ..., description="从原始列表中筛选出的具有核心代表意义的名词列表。"
    )


async def filter_tags_with_llm(
    llm: ProfileChatModel, tags: list[str], end_user_id: str
) -> list[str]:
    """Keep representative nouns; fall back to the raw list on any LLM failure."""

    messages = [
        {"role": "system", "content": render_tag_filter_system_prompt()},
        {"role": "user", "content": render_tag_filter_user_prompt(tags)},
    ]
    try:
        response = await llm.call_structured(messages, FilteredTags)
        return response.meaningful_tags
    except Exception as exc:
        logger.warning(
            "LLM tag filtering unavailable, using raw tags: end_user_id=%s error=%s",
            end_user_id,
            type(exc).__name__,
        )
        return tags


async def get_hot_memory_tags(
    graph: GenerationGraphRepository,
    llm: ProfileChatModel,
    end_user_id: str,
    limit: int,
) -> list[tuple[str, int]]:
    """Top-N LLM-filtered entity tags with their real occurrence counts."""

    raw_tags = await graph.raw_entity_tags(end_user_id, TAG_QUERY_LIMIT)
    if not raw_tags:
        return []
    meaningful = await filter_tags_with_llm(llm, [name for name, _ in raw_tags], end_user_id)
    return [(name, frequency) for name, frequency in raw_tags if name in meaningful][:limit]
