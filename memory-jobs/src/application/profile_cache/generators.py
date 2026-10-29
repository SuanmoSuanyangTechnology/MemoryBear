"""LLM-facing generators for the profile context.

The domain owns the ``InsightContent`` / ``SummarySections`` values and their
invariants; this module owns the provider-shaped parts: the structured-output
schemas, prompt invocation, tag filtering and the summary parser. It maps the
raw model output into domain values.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from src.domain.ports.llm import ChatModel
from src.domain.ports.prompts import PromptCatalog
from src.domain.profile.model import InsightContent, SummarySections
from src.domain.profile.read_models import EntityTag, StatementSample
from src.domain.shared.language import Language

logger = logging.getLogger(__name__)

SUMMARY_ENTITY_QUERY_LIMIT = 40
SUMMARY_STATEMENT_QUERY_LIMIT = 100
SUMMARY_PROMPT_ENTITY_LINES = 20
SUMMARY_PROMPT_STATEMENT_LINES = 20


class MemoryInsightFinding(BaseModel):
    finding: str = Field(min_length=1, max_length=160)
    recommended_action: str = Field(min_length=1, max_length=120)


class MemoryInsightOutput(BaseModel):
    overview: str = Field(min_length=1, max_length=240)
    key_findings: list[MemoryInsightFinding] = Field(default_factory=list, max_length=3)


class FilteredTags(BaseModel):
    """LLM-filtered core tag list."""

    meaningful_tags: list[str] = Field(
        ..., description="从原始列表中筛选出的具有核心代表意义的名词列表。"
    )


class MemoryInsightGenerator:
    def __init__(self, chat_model: ChatModel, prompts: PromptCatalog) -> None:
        self._chat_model = chat_model
        self._prompts = prompts

    def generate(
        self,
        metadata_input: dict[str, list[str]],
        fallback_statements: list[str],
        language: Language,
    ) -> InsightContent:
        prompt = self._prompts.memory_insight(
            metadata_input, fallback_statements, language.value
        )
        raw_output = self._chat_model.call_structured(
            [{"role": "user", "content": prompt}],
            MemoryInsightOutput,
        )
        output = (
            raw_output
            if isinstance(raw_output, MemoryInsightOutput)
            else MemoryInsightOutput.model_validate(raw_output)
        )
        return InsightContent.create(
            output.overview,
            ((item.finding, item.recommended_action) for item in output.key_findings),
        )


class UserSummaryGenerator:
    def __init__(self, chat_model: ChatModel, prompts: PromptCatalog) -> None:
        self._chat_model = chat_model
        self._prompts = prompts

    def generate(
        self,
        end_user_id: str,
        display_name: str | None,
        entity_tags: list[EntityTag],
        statements: list[StatementSample],
        language: Language,
    ) -> SummarySections:
        entities = self._hot_tags(end_user_id, entity_tags)
        entity_lines = [
            f"{name} ({frequency})" for name, frequency in entities
        ][:SUMMARY_PROMPT_ENTITY_LINES]
        statement_samples = [
            sample.statement.strip()
            for sample in statements
            if sample.statement.strip()
        ][:SUMMARY_PROMPT_STATEMENT_LINES]

        empty = "(空)" if language is Language.ZH else "(empty)"
        prompt = self._prompts.user_summary(
            user_id=end_user_id,
            entities=", ".join(entity_lines) if entity_lines else empty,
            statements=" | ".join(statement_samples) if statement_samples else empty,
            language=language.value,
            user_display_name=display_name,
        )
        response = self._chat_model.invoke([{"role": "user", "content": prompt}])
        return parse_summary_sections(response.text)

    def _hot_tags(
        self, end_user_id: str, entity_tags: list[EntityTag]
    ) -> list[tuple[str, int]]:
        """Top-N LLM-filtered entity tags with their real occurrence counts."""

        raw_tags = [(tag.name, tag.frequency) for tag in entity_tags]
        if not raw_tags:
            return []
        meaningful = self._filter_tags(
            [name for name, _ in raw_tags], end_user_id
        )
        return [
            (name, frequency)
            for name, frequency in raw_tags
            if name in meaningful
        ][:SUMMARY_ENTITY_QUERY_LIMIT]

    def _filter_tags(self, tags: list[str], end_user_id: str) -> list[str]:
        """Keep representative nouns; fall back to the raw list on any failure."""

        messages: list[Any] = [
            {"role": "system", "content": self._prompts.tag_filter_system()},
            {"role": "user", "content": self._prompts.tag_filter_user(tags)},
        ]
        try:
            response = self._chat_model.call_structured(messages, FilteredTags)
            return response.meaningful_tags
        except Exception as exc:
            logger.warning(
                "LLM tag filtering unavailable, using raw tags: end_user_id=%s error=%s",
                end_user_id,
                type(exc).__name__,
            )
            return tags


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


def parse_summary_sections(full_response: str) -> SummarySections:
    """Extract the four labelled sections (zh/en headings)."""

    def section(headings: str) -> str:
        match = re.search(
            rf"【(?:{headings})】\s*\n(.*?)(?=\n【|$)",
            full_response,
            re.DOTALL,
        )
        return _clean_reflection_content(match.group(1).strip() if match else "")

    return SummarySections(
        basic_introduction=section("基本介绍|Basic Introduction"),
        personality=section("性格特点|Personality Traits"),
        core_values=section("核心价值观|Core Values"),
        one_sentence=section("一句话总结|One-Sentence Summary"),
    )
