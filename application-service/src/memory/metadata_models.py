"""Read-side projection helpers for the end-user metadata structure.

Frozen port of the display-projection subset of the monolith
``app/core/memory/models/metadata_models``: the new-structure top-level keys
(``identity_background`` / ``relationships`` / ``domain_specific_info``) store
entries as ``<level2> | <level3> | <content>`` strings, and the display
projection regroups them by the 13 shared level-2 keys with the level-3 part
dropped.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

IDENTITY_BACKGROUND_SUBCATEGORIES: tuple[str, ...] = (
    "name",
    "age",
    "from",
    "residence",
    "education",
    "occupation",
    "family",
    "religion_or_politics",
    "health",
)

RELATION_TYPES: tuple[str, ...] = (
    "person",
    "group",
    "object",
    "pet",
)

# The 13 shared level-2 keys used by the display / L0 projections, order fixed.
PROFILE_DISPLAY_KEYS: tuple[str, ...] = IDENTITY_BACKGROUND_SUBCATEGORIES + RELATION_TYPES

# Historical eight fields: no longer generated, still read and merged.
LEGACY_METADATA_FIELDS: tuple[str, ...] = (
    "core_facts",
    "traits",
    "relations",
    "goals",
    "interests",
    "beliefs_or_stances",
    "anchors",
    "events",
)


def split_metadata_entry(item: Any) -> tuple[str, str, str] | None:
    """Parse one stored entry into (level2, level3, content).

    A two-segment entry is read as an empty level-3 key; non-strings, other
    segment counts and empty content resolve to None.
    """
    if not isinstance(item, str):
        return None
    parts = [part.strip() for part in item.strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    if len(parts) != 3 or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def normalize_new_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Copies of the three new-structure keys; missing or mistyped values
    default to list / list / dict[str, list]."""
    source = meta if isinstance(meta, dict) else {}
    identity_background = source.get("identity_background")
    relationships = source.get("relationships")
    domain_specific_info = source.get("domain_specific_info")
    return {
        "identity_background": (
            list(identity_background) if isinstance(identity_background, list) else []
        ),
        "relationships": (
            list(relationships) if isinstance(relationships, list) else []
        ),
        "domain_specific_info": (
            {k: list(v) for k, v in domain_specific_info.items() if isinstance(v, list)}
            if isinstance(domain_specific_info, dict)
            else {}
        ),
    }


def _iter_profile_entries(
    meta: dict[str, Any] | None,
) -> Iterator[tuple[str, str, str]]:
    """Yield parseable identity_background / relationships entries whose
    level-2 key is one of the 13 shared keys."""
    normalized = normalize_new_metadata(meta)
    for field in ("identity_background", "relationships"):
        for item in normalized[field]:
            triplet = split_metadata_entry(item)
            if triplet and triplet[0] in PROFILE_DISPLAY_KEYS:
                yield triplet


def project_display_profile(
    meta: dict[str, Any] | None,
    limit_per_key: int | None = None,
) -> dict[str, list[str]]:
    """Display projection (GET profile, trace profile).

    Groups by the level-2 key and keeps the content segment as the value; the
    level-3 segment is dropped. Equal contents within one key are deduplicated
    (first occurrence kept). Output follows the PROFILE_DISPLAY_KEYS order and
    omits keys without values; ``limit_per_key=None`` keeps every entry.
    """
    grouped: dict[str, list[str]] = {}
    for level2, _level3, content in _iter_profile_entries(meta):
        values = grouped.setdefault(level2, [])
        if content not in values:
            values.append(content)
    result: dict[str, list[str]] = {}
    for key in PROFILE_DISPLAY_KEYS:
        values = grouped.get(key)
        if values:
            result[key] = values if limit_per_key is None else values[:limit_per_key]
    return result


__all__ = [
    "IDENTITY_BACKGROUND_SUBCATEGORIES",
    "LEGACY_METADATA_FIELDS",
    "PROFILE_DISPLAY_KEYS",
    "RELATION_TYPES",
    "normalize_new_metadata",
    "project_display_profile",
    "split_metadata_entry",
]
