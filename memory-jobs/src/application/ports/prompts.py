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

    def interest_filter(
        self,
        candidates: list[dict[str, object]],
        language: str,
        evidence: list[dict[str, object]] | None = None,
        existing_interests: list[str] | None = None,
    ) -> str: ...
