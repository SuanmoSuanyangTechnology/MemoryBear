"""User-summary generation pipeline.

Ports the API's ``analytics_user_summary``: hot entities and recent statements
become the prompt input, one chat call produces four labelled sections, and the
sections are parsed with the same tolerant regexes. Persistence stays in the
service layer so this module is pure generation.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from src.infrastructure.model_service import ProfileChatModel
from src.modules.analytics.generation.hot_tags import get_hot_memory_tags
from src.modules.analytics.repository.generation_graph import GenerationGraphRepository
from src.modules.analytics.repository.memory_generation import MemoryGenerationRepository
from src.modules.shared import ChatModelProvider
from src.prompts import render_user_summary_prompt

logger = logging.getLogger(__name__)

SUMMARY_ENTITY_LIMIT = 40
SUMMARY_STATEMENT_LIMIT = 100
SUMMARY_PROMPT_ENTITY_LINES = 20
SUMMARY_PROMPT_STATEMENT_LINES = 20


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


async def generate_user_summary(
    repository: MemoryGenerationRepository,
    graph: GenerationGraphRepository,
    resolver: ChatModelProvider,
    end_user_id: str,
    language: str = "zh",
    *,
    llm: ProfileChatModel | None = None,
    display_name: str | None = None,
) -> dict[str, str]:
    """Generate (do not persist) the four summary sections.

    ``display_name`` and ``llm`` come from the caller's already-resolved end user
    and workspace plan, so the generation entrypoint neither re-reads
    ``other_name`` nor re-resolves the model configuration.
    """

    if display_name is None:
        display_name = "该用户" if language == "zh" else "the user"
        other_name = await repository.other_name(UUID(end_user_id))
        if other_name:
            display_name = other_name

    if llm is None:
        llm = await resolver.chat_model(UUID(end_user_id))

    entities = await get_hot_memory_tags(
        graph, llm, end_user_id, limit=SUMMARY_ENTITY_LIMIT
    )
    statements = await graph.recent_statements(end_user_id, SUMMARY_STATEMENT_LIMIT)

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
    response = await llm.ainvoke([{"role": "user", "content": prompt}])
    return parse_summary_sections(response.text)
