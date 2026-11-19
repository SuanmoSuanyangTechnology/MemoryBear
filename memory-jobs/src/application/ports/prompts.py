"""Prompt rendering port.

Prompts are an application artifact (text shaped for the model), but rendering
them currently uses Jinja2. The application generators depend on this port; the
Jinja adapter lives in infrastructure.
"""

from __future__ import annotations

from typing import Protocol


class PromptCatalog(Protocol):
    def memory_insight(
        self,
        metadata_input: dict[str, list[str]],
        fallback_statements: list[str],
        language: str,
    ) -> str: ...

    def user_summary(
        self,
        *,
        user_id: str,
        entities: str,
        statements: str,
        language: str,
        user_display_name: str | None,
    ) -> str: ...

    def tag_filter_system(self) -> str: ...

    def tag_filter_user(self, tags: list[str]) -> str: ...

    def metadata_migration(
        self, *, entity_id: str, language: str, input_json: dict[str, object]
    ) -> str: ...

    def interest_filter(
        self,
        candidates: list[dict[str, object]],
        language: str,
        evidence: list[dict[str, object]] | None = None,
        existing_interests: list[str] | None = None,
    ) -> str: ...

    def rag_profile_summary(
        self, chunk_texts: str, json_schema: dict, max_words: int, language: str
    ) -> str: ...

    def rag_profile_tags(self, chunk: str, language: str) -> str: ...

    def rag_profile_domain(self, chunk: str) -> str: ...

    def rag_profile_personas(self, chunks: list[str], language: str) -> str: ...

    def rag_profile_insight(
        self,
        domain_distribution: str,
        active_periods: object,
        social_connections: object,
        language: str,
    ) -> str: ...

    def emotion_suggestions(
        self,
        health_data: dict,
        patterns: dict,
        user_profile: dict,
        language: str,
        emotion_distribution_json: str,
    ) -> str: ...

    def forgetting_summary(self, pair: dict) -> str: ...

    def conversation_summary_system(self) -> str: ...

    def conversation_summary_user(self, language: str, conversation: str) -> str: ...
