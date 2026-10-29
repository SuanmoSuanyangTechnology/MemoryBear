"""Jinja2 implementation of the prompt-catalog port."""

from __future__ import annotations

from src.prompts import (
    render_interest_filter_prompt,
    render_memory_insight_prompt,
    render_tag_filter_system_prompt,
    render_tag_filter_user_prompt,
    render_user_summary_prompt,
)


class JinjaPromptCatalog:
    def memory_insight(
        self,
        metadata_input: dict[str, list[str]],
        fallback_statements: list[str],
        language: str,
    ) -> str:
        return render_memory_insight_prompt(
            metadata_input, fallback_statements, language
        )

    def user_summary(
        self,
        *,
        user_id: str,
        entities: str,
        statements: str,
        language: str,
        user_display_name: str | None,
    ) -> str:
        return render_user_summary_prompt(
            user_id=user_id,
            entities=entities,
            statements=statements,
            language=language,
            user_display_name=user_display_name,
        )

    def tag_filter_system(self) -> str:
        return render_tag_filter_system_prompt()

    def tag_filter_user(self, tags: list[str]) -> str:
        return render_tag_filter_user_prompt(tags)

    def interest_filter(
        self,
        candidates: list[dict[str, object]],
        language: str,
        evidence: list[dict[str, object]] | None = None,
        existing_interests: list[str] | None = None,
    ) -> str:
        return render_interest_filter_prompt(
            candidates,
            language=language,
            evidence=evidence,
            existing_interests=existing_interests,
        )
