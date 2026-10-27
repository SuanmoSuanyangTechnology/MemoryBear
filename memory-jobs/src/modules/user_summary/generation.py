"""User-summary generation, ported to blocking clients.

Ports ``memory-profile/src/modules/analytics/generation/user_summary.py`` and the
``hot_tags`` helper it depends on: hot entities and recent statements become the
prompt input, one chat call produces four labelled sections, and the sections are
parsed with the same tolerant regexes. Persistence stays in the service layer.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from pydantic import BaseModel, Field

from src.infrastructure.model_service import SyncChatModel
from src.modules.shared.repositories.generation_graph import GenerationGraphRepository
from src.modules.shared.repositories.memory_generation import MemoryGenerationRepository
from src.prompts import (
    render_tag_filter_system_prompt,
    render_tag_filter_user_prompt,
    render_user_summary_prompt,
)

logger = logging.getLogger(__name__)

SUMMARY_ENTITY_LIMIT = 40
SUMMARY_STATEMENT_LIMIT = 100
SUMMARY_PROMPT_ENTITY_LINES = 20
SUMMARY_PROMPT_STATEMENT_LINES = 20

#: 查询更多候选给 LLM（api ``get_hot_memory_tags`` 同值）。
TAG_QUERY_LIMIT = 40


class FilteredTags(BaseModel):
    """LLM 筛选后的核心标签列表。"""

    meaningful_tags: list[str] = Field(
        ..., description="从原始列表中筛选出的具有核心代表意义的名词列表。"
    )


def filter_tags_with_llm(llm: SyncChatModel, tags: list[str], end_user_id: str) -> list[str]:
    """Keep representative nouns; fall back to the raw list on any LLM failure."""

    messages = [
        {"role": "system", "content": render_tag_filter_system_prompt()},
        {"role": "user", "content": render_tag_filter_user_prompt(tags)},
    ]
    try:
        response = llm.call_structured(messages, FilteredTags)
        return response.meaningful_tags
    except Exception as exc:
        logger.warning(
            "LLM tag filtering unavailable, using raw tags: end_user_id=%s error=%s",
            end_user_id,
            type(exc).__name__,
        )
        return tags


def get_hot_memory_tags(
    graph: GenerationGraphRepository, llm: SyncChatModel, end_user_id: str, limit: int
) -> list[tuple[str, int]]:
    """Top-N LLM-filtered entity tags with their real occurrence counts."""

    raw_tags = graph.raw_entity_tags(end_user_id, TAG_QUERY_LIMIT)
    if not raw_tags:
        return []
    meaningful = filter_tags_with_llm(llm, [name for name, _ in raw_tags], end_user_id)
    return [(name, frequency) for name, frequency in raw_tags if name in meaningful][:limit]


def _clean_reflection_content(text: str) -> str:
    """Drop trailing reflection sections the model sometimes appends."""

    if not text:
        return text
    if "---" in text:
        text = text.split("---")[0].strip()
    if "**Step" in text:
        text = text.split("**Step")[0].strip()
    if "Self-Review" in text or "self-review" in text:
        text = re.sub(
            r"[\-\*]*\s*Self-Review.*$", "", text, flags=re.IGNORECASE | re.DOTALL
        ).strip()
    return text


def parse_summary_sections(full_response: str) -> dict[str, str]:
    """Extract the four labelled sections (zh/en headings)."""

    user_summary_match = re.search(
        r"【(?:基本介绍|Basic Introduction)】\s*\n(.*?)(?=\n【|$)",
        full_response,
        re.DOTALL,
    )
    personality_match = re.search(
        r"【(?:性格特点|Personality Traits)】\s*\n(.*?)(?=\n【|$)",
        full_response,
        re.DOTALL,
    )
    core_values_match = re.search(
        r"【(?:核心价值观|Core Values)】\s*\n(.*?)(?=\n【|$)",
        full_response,
        re.DOTALL,
    )
    one_sentence_match = re.search(
        r"【(?:一句话总结|One-Sentence Summary)】\s*\n(.*?)(?=\n【|$)",
        full_response,
        re.DOTALL,
    )
    return {
        "user_summary": _clean_reflection_content(
            user_summary_match.group(1).strip() if user_summary_match else ""
        ),
        "personality": _clean_reflection_content(
            personality_match.group(1).strip() if personality_match else ""
        ),
        "core_values": _clean_reflection_content(
            core_values_match.group(1).strip() if core_values_match else ""
        ),
        "one_sentence": _clean_reflection_content(
            one_sentence_match.group(1).strip() if one_sentence_match else ""
        ),
    }


def generate_user_summary(
    repository: MemoryGenerationRepository,
    graph: GenerationGraphRepository,
    llm: SyncChatModel,
    end_user_id: str,
    language: str = "zh",
    *,
    display_name: str | None = None,
) -> dict[str, str]:
    """Generate (do not persist) the four summary sections."""

    if display_name is None:
        display_name = "该用户" if language == "zh" else "the user"
        other_name = repository.other_name(UUID(end_user_id))
        if other_name:
            display_name = other_name

    entities = get_hot_memory_tags(graph, llm, end_user_id, limit=SUMMARY_ENTITY_LIMIT)
    statements = graph.recent_statements(end_user_id, SUMMARY_STATEMENT_LIMIT)

    entity_lines = [f"{name} ({frequency})" for name, frequency in entities][
        :SUMMARY_PROMPT_ENTITY_LINES
    ]
    statement_samples = [
        str(item.get("statement", "")).strip()
        for item in statements
        if str(item.get("statement", "")).strip()
    ][:SUMMARY_PROMPT_STATEMENT_LINES]

    empty = "(空)" if language == "zh" else "(empty)"
    prompt = render_user_summary_prompt(
        user_id=end_user_id,
        entities=", ".join(entity_lines) if entity_lines else empty,
        statements=" | ".join(statement_samples) if statement_samples else empty,
        language=language,
        user_display_name=display_name,
    )
    response = llm.invoke([{"role": "user", "content": prompt}])
    return parse_summary_sections(response.text)
