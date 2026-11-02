"""Bounded insight input projection.

The caps and ordering are the API's: metadata groups are flattened, cleaned,
deduplicated case-insensitively, capped per field and in total, then topped up
with recent statements only when the metadata is thin.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from src.application.profile_cache.metadata import (
    flatten_domain_specific_info,
    split_metadata_entry,
)
from src.application.profile_cache.scene_names import (
    drop_deleted_scene_entries,
    project_domain_names,
)
from src.domain.shared.text import compact_text

MEMORY_INSIGHT_META_FIELDS = (
    "identity_background",
    "relationships",
    "domain_specific_info",
    "core_facts",
    "traits",
    "relations",
    "goals",
    "interests",
    "beliefs_or_stances",
    "anchors",
    "events",
)
MEMORY_INSIGHT_META_FIELD_LIMIT = 5
MEMORY_INSIGHT_META_TOTAL_CHAR_LIMIT = 6000
MEMORY_INSIGHT_STATEMENT_LIMIT = 12
MEMORY_INSIGHT_INPUT_TEXT_LIMIT = 200
MEMORY_INSIGHT_MIN_ENTRIES = 2


def _metadata_groups(metadata: dict[str, object]) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = {}
    for field in MEMORY_INSIGHT_META_FIELDS:
        raw_values = metadata.get(field)
        if field in ("identity_background", "relationships"):
            if not isinstance(raw_values, list):
                continue
            for value in cast(list[object], raw_values):
                triplet = split_metadata_entry(value)
                if not triplet:
                    continue
                l2, l3, content = triplet
                text = f"{l3} | {content}" if l3 else content
                groups.setdefault(f"{field}.{l2}", []).append(text)
        elif field == "domain_specific_info":
            for scene_key, field_key, content in flatten_domain_specific_info(raw_values):
                groups.setdefault(f"{field}.{scene_key}.{field_key}", []).append(content)
        elif isinstance(raw_values, list):
            groups[field] = [v for v in cast(list[object], raw_values) if isinstance(v, str)]
    return list(groups.items())


def build_memory_insight_metadata_input(
    meta_data: object, scene_names_by_id: Mapping[str, str] | None = None
) -> dict[str, list[str]]:
    """Bounded metadata projection (api limits, same order and truncation).

    ``domain_specific_info`` is projected to scene names first: uuid keys whose
    scene no longer exists are dropped, the rest are renamed. Legacy name-keyed
    entries pass through, matching the API. ``None`` means the lookup was
    unavailable, so the projection is skipped and the stored keys are kept
    (dropping them would turn an outage into data loss).
    """

    if not isinstance(meta_data, dict):
        return {}
    metadata = dict(cast(dict[str, object], meta_data))
    if scene_names_by_id is not None:
        metadata["domain_specific_info"] = project_domain_names(
            drop_deleted_scene_entries(
                metadata.get("domain_specific_info"), scene_names_by_id
            ),
            scene_names_by_id,
        )

    result: dict[str, list[str]] = {}
    total_chars = 0
    for group_key, values in _metadata_groups(metadata):
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = compact_text(value, MEMORY_INSIGHT_INPUT_TEXT_LIMIT)
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            if total_chars + len(normalized) > MEMORY_INSIGHT_META_TOTAL_CHAR_LIMIT:
                if cleaned:
                    result[group_key] = cleaned
                return result
            seen.add(key)
            cleaned.append(normalized)
            total_chars += len(normalized)
            if len(cleaned) >= MEMORY_INSIGHT_META_FIELD_LIMIT:
                break
        if cleaned:
            result[group_key] = cleaned
    return result


def count_memory_insight_entries(metadata_input: dict[str, list[str]]) -> int:
    return sum(len(values) for values in metadata_input.values())


def build_memory_insight_statement_input(
    statements: Sequence[object],
    metadata_input: dict[str, list[str]],
) -> list[str]:
    """Clean statements and drop text already present in the profile input."""

    seen = {value.casefold() for values in metadata_input.values() for value in values}
    result: list[str] = []
    for value in statements:
        if not isinstance(value, str):
            continue
        normalized = compact_text(value, MEMORY_INSIGHT_INPUT_TEXT_LIMIT)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= MEMORY_INSIGHT_STATEMENT_LIMIT:
            break
    return result
