"""Jinja2 implementation of the prompt-catalog port."""

from __future__ import annotations

from src.prompts import (
    render_interest_filter_prompt,
    render_memory_insight_prompt,
    render_metadata_migration_prompt,
    render_tag_filter_system_prompt,
    render_tag_filter_user_prompt,
    render_user_summary_prompt,
)
from src.prompts.loader import render_prompt


class JinjaPromptCatalog:
    def memory_insight(
        self,
        metadata_input: dict[str, list[str]],
        fallback_statements: list[str],
        language: str,
    ) -> str:
        return render_memory_insight_prompt(metadata_input, fallback_statements, language)

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

    def metadata_migration(
        self, *, entity_id: str, language: str, input_json: dict[str, object]
    ) -> str:
        return render_metadata_migration_prompt(
            entity_id=entity_id, language=language, input_json=input_json
        )

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

    def rag_profile_summary(
        self, chunk_texts: str, json_schema: dict, max_words: int, language: str
    ) -> str:
        return render_prompt(
            "rag_memory_summary",
            chunk_texts=chunk_texts,
            json_schema=json_schema,
            max_words=max_words,
            language=language,
        )

    def rag_profile_tags(self, chunk: str, language: str) -> str:
        return render_prompt("rag_tags", chunk=chunk, language=language)

    def rag_profile_domain(self, chunk: str) -> str:
        return render_prompt("rag_domain", chunk=chunk)

    def rag_profile_personas(self, chunks: list[str], language: str) -> str:
        return render_prompt("rag_personas", chunks=chunks, language=language)

    def rag_profile_insight(
        self,
        domain_distribution: str,
        active_periods: object,
        social_connections: object,
        language: str,
    ) -> str:
        return render_prompt(
            "rag_memory_insight",
            domain_distribution=domain_distribution,
            active_periods=active_periods,
            social_connections=social_connections,
            language=language,
        )

    def emotion_suggestions(
        self,
        health_data: dict,
        patterns: dict,
        user_profile: dict,
        language: str,
        emotion_distribution_json: str,
    ) -> str:
        return render_prompt(
            "generate_emotion_suggestions",
            health_data=health_data,
            patterns=patterns,
            user_profile=user_profile,
            language=language,
            emotion_distribution_json=emotion_distribution_json,
        )

    def forgetting_summary(self, pair: dict) -> str:
        return render_prompt("forget_summary", pair=pair)

    def conversation_summary_system(self) -> str:
        return render_prompt("conversation_summary_system")

    def conversation_summary_user(self, language: str, conversation: str) -> str:
        return render_prompt(
            "conversation_summary_user", language=language, conversation=conversation
        )
