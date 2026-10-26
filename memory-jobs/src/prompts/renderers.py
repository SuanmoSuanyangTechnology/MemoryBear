"""Named renderers for the generation prompts.

Each renderer owns one prompt's context shape and delegates the text itself to
:func:`src.prompts.loader.render_prompt`. Templates are byte-identical copies of
the API's prompt files (and of memory-profile's copies), so every service sends
the model the same text for the same input.
"""

from __future__ import annotations

import json

from src.prompts.loader import render_prompt


def render_memory_insight_prompt(
    metadata_input: dict[str, list[str]],
    fallback_statements: list[str],
    language: str = "zh",
) -> str:
    """Grounded insight prompt: profile metadata plus statement fallback."""

    payload = {
        "profile_metadata": {
            field: values for field, values in metadata_input.items() if values
        },
        "fallback_statements": fallback_statements,
    }
    return render_prompt(
        "memory_insight_grounded",
        input_json=json.dumps(payload, ensure_ascii=False),
        language=language,
    )


def render_user_summary_prompt(
    user_id: str,
    entities: str,
    statements: str,
    language: str = "zh",
    user_display_name: str | None = None,
) -> str:
    """Four-section user summary prompt."""

    if user_display_name is None:
        user_display_name = "该用户" if language == "zh" else "the user"
    return render_prompt(
        "user_summary",
        user_id=user_id,
        entities=entities,
        statements=statements,
        language=language,
        user_display_name=user_display_name,
    )


def render_interest_filter_prompt(
    candidates: list[dict[str, object]],
    language: str = "zh",
    evidence: list[dict[str, object]] | None = None,
    existing_interests: list[str] | None = None,
) -> str:
    """Interest grouping prompt (candidates, optional statement evidence)."""

    return render_prompt(
        "interest_filter",
        candidates_json=json.dumps(candidates, ensure_ascii=False),
        evidence_json=json.dumps(evidence, ensure_ascii=False) if evidence else None,
        existing_interests_json=(
            json.dumps(existing_interests, ensure_ascii=False)
            if existing_interests
            else None
        ),
        language=language,
    )


def render_tag_filter_system_prompt() -> str:
    """System prompt that filters and merges raw entity tags."""

    return render_prompt("hot_tags_filter_system")


def render_tag_filter_user_prompt(tags: list[str]) -> str:
    """User message carrying the candidate tags for the tag filter."""

    return render_prompt("hot_tags_filter_user", tag_list=", ".join(tags))
