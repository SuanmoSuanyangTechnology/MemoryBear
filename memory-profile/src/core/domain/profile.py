"""User-profile metadata read projection (pure value shaping).

Ported from the host's ``metadata_models`` read helpers. Only the read-side
projection is kept: the write-side merge/normalization pipeline stays in the
API host because this service never writes profile metadata.

The 13 public keys (9 identity-background categories + 4 relation types) are
wire-frozen — the streaming display contract lists them explicitly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.core.domain.enums import MemorySource
from src.core.domain.models import MemoryItem

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
RELATION_TYPES: tuple[str, ...] = ("person", "group", "object", "pet")
PROFILE_DISPLAY_KEYS: tuple[str, ...] = (
    IDENTITY_BACKGROUND_SUBCATEGORIES + RELATION_TYPES
)

#: ``entity_type`` of the graph node that carries a terminal user's profile.
USER_PROFILE_ENTITY_TYPE = "用户"

#: Historical profile fields. They are still read from storage and merged into
#: the L0 record, but the public ``/v1`` read response drops them from the
#: terminal-user profile item's ``data`` (host ``_serialize_public_memory``).
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


def is_user_profile(memory: MemoryItem, end_user_id: str) -> bool:
    """Identify the user-profile node independently of its list position.

    Covers both the synthesized L0 item (``id == end_user_id``) and a real
    graph node whose ``entity_type`` marks the terminal user.
    """

    source = memory.source
    source_value = source.value if isinstance(source, MemorySource) else str(source)
    if source_value != MemorySource.EXTRACTED_ENTITY.value:
        return False
    if end_user_id and memory.id == end_user_id:
        return True
    return memory.data.get("entity_type") == USER_PROFILE_ENTITY_TYPE


def split_metadata_entry(item: Any) -> tuple[str, str, str] | None:
    """Parse ``"<key> | <level3> | <content>"``; two-part entries get an empty level3."""

    if not isinstance(item, str):
        return None
    parts = [part.strip() for part in item.strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    if len(parts) != 3 or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def normalize_new_metadata(meta: Any) -> dict[str, Any]:
    """Return the three-key shape, filling defaults when absent or mistyped."""

    meta = meta if isinstance(meta, dict) else {}
    identity_background = meta.get("identity_background")
    relationships = meta.get("relationships")
    domain_specific_info = meta.get("domain_specific_info")
    return {
        "identity_background": (
            list(identity_background) if isinstance(identity_background, list) else []
        ),
        "relationships": list(relationships) if isinstance(relationships, list) else [],
        "domain_specific_info": (
            {
                key: list(value)
                for key, value in domain_specific_info.items()
                if isinstance(value, list)
            }
            if isinstance(domain_specific_info, dict)
            else {}
        ),
    }


def iter_profile_entries(meta: Any) -> Iterator[tuple[str, str, str]]:
    """Yield parsed entries whose key belongs to the 13 public display keys."""

    normalized = normalize_new_metadata(meta)
    for field in ("identity_background", "relationships"):
        for item in normalized[field]:
            triplet = split_metadata_entry(item)
            if triplet and triplet[0] in PROFILE_DISPLAY_KEYS:
                yield triplet


def project_display_profile(
    meta: Any,
    limit_per_key: int | None = None,
) -> dict[str, list[str]]:
    """Project to the display shape: level-2 key -> deduplicated level-3 texts."""

    grouped: dict[str, list[str]] = {}
    for level2, _level3, content in iter_profile_entries(meta):
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
    "USER_PROFILE_ENTITY_TYPE",
    "is_user_profile",
    "iter_profile_entries",
    "normalize_new_metadata",
    "project_display_profile",
    "split_metadata_entry",
]