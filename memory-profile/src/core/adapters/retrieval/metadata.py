"""Read-side user-profile metadata helpers.

The shared parsing/projection helpers live in :mod:`src.core.domain.profile`
(they only shape values, so the domain layer owns them). This module adds the
read-only pieces the L0 profile builder needs, plus the request-local metadata
scope projection ported from ``api/app/core/memory/read_services/metadata_scope.py``.

Known gap: the host resolves ``memory.metadata_scope`` through its plugin
registry (enterprise only) to bound the profile to the calling application. That
plugin is not available in this service, so :func:`scoped_metadata` always
applies the documented ``default_only`` behaviour (no application scope).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from src.core.domain.profile import (
    IDENTITY_BACKGROUND_SUBCATEGORIES,
    PROFILE_DISPLAY_KEYS,
    RELATION_TYPES,
    normalize_new_metadata,
    split_metadata_entry,
)

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
NEW_METADATA_FIELDS: tuple[str, ...] = (
    "identity_background",
    "relationships",
    "domain_specific_info",
)


def format_entry_triplet(level2: str, level3: str, content: str) -> str:
    level2_clean = (level2 or "").strip()
    level3_clean = (level3 or "").strip()
    content_clean = (content or "").strip()
    if level3_clean:
        return f"{level2_clean} | {level3_clean} | {content_clean}"
    return f"{level2_clean} | | {content_clean}"


def entry_key(item: str) -> str:
    parts = [part.strip() for part in (item or "").strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    return "|".join(parts)


def _iter_profile_entries(meta: Any):
    normalized = normalize_new_metadata(meta)
    for field_name in ("identity_background", "relationships"):
        for item in normalized[field_name]:
            triplet = split_metadata_entry(item)
            if triplet and triplet[0] in PROFILE_DISPLAY_KEYS:
                yield triplet


def project_l0_profile(meta: Any) -> dict[str, list[str]]:
    """L0 projection fed to the model: level-2 key -> full three-part entries."""

    grouped: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for level2, level3, content in _iter_profile_entries(meta):
        entry = format_entry_triplet(level2, level3, content)
        key = entry_key(entry)
        if key in seen.setdefault(level2, set()):
            continue
        seen[level2].add(key)
        grouped.setdefault(level2, []).append(entry)
    return {key: grouped[key] for key in PROFILE_DISPLAY_KEYS if key in grouped}


def is_scene_type_id(key: object) -> bool:
    """Whether a ``domain_specific_info`` key is a scene type uuid."""

    if not isinstance(key, str):
        return False
    try:
        UUID(key)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def drop_deleted_scene_entries(
    domain_specific_info: Any,
    scene_names_by_id: dict[str, str] | None = None,
) -> Any:
    """Drop uuid-keyed scene entries whose scene type no longer exists."""

    if not isinstance(domain_specific_info, dict):
        return domain_specific_info
    names = scene_names_by_id or {}
    return {
        key: items
        for key, items in domain_specific_info.items()
        if not (is_scene_type_id(key) and key not in names)
    }


def _union_entries(base: list[Any], extra: list[Any]) -> list[Any]:
    result = list(base)
    seen = {entry_key(item) for item in result if isinstance(item, str)}
    for item in extra:
        if not isinstance(item, str):
            if item not in result:
                result.append(item)
            continue
        key = entry_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def project_domain_names(
    domain_specific_info: Any,
    scene_names_by_id: dict[str, str] | None = None,
) -> dict[str, list[Any]]:
    """Replace scene ids with names; unmapped keys pass through, collisions merge."""

    if not isinstance(domain_specific_info, dict):
        return {}
    names = scene_names_by_id or {}
    remapped: dict[str, list[Any]] = {}
    for key, items in domain_specific_info.items():
        if not isinstance(items, list):
            continue
        destination = names.get(key, key)
        remapped[destination] = _union_entries(remapped.get(destination, []), items)
    return remapped


def _filter_entries(value: Any, allowed_keys: set[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        parsed = split_metadata_entry(item)
        if parsed is None:
            continue
        field_key, level3, _ = parsed
        if allowed_keys is not None and (field_key not in allowed_keys or level3):
            continue
        result.append(item)
    return result


def project_metadata(record: Any, scope: dict[str, Any] | str | None) -> dict[str, Any]:
    """Request-local profile projection (host ``metadata_scope.project_metadata``)."""

    result = deepcopy(record) if isinstance(record, dict) else {}
    for key in ("identity_background", "relationships"):
        value = result.get(key)
        result[key] = (
            [item for item in value if isinstance(item, str) and item.strip()]
            if isinstance(value, list)
            else []
        )

    raw_scene_type_id = scope.get("scene_type_id") if isinstance(scope, dict) else scope
    scene_type_id = str(raw_scene_type_id) if raw_scene_type_id else None
    raw_field_keys = scope.get("field_keys") if isinstance(scope, dict) else None
    field_keys = set(raw_field_keys) if raw_field_keys is not None else None
    domains = result.get("domain_specific_info")
    value = domains.get(scene_type_id) if isinstance(domains, dict) and scene_type_id else None
    if field_keys is not None:
        filtered = _filter_entries(value, field_keys)
    else:
        filtered = (
            [item for item in value if isinstance(item, str) and item.strip()]
            if isinstance(value, list)
            else []
        )
    result["domain_specific_info"] = {scene_type_id: filtered} if scene_type_id and filtered else {}
    return result


def scoped_metadata(record: Any, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the resolved app scope, or the host ``default_only`` fallback."""

    return project_metadata(record, scope)


__all__ = [
    "IDENTITY_BACKGROUND_SUBCATEGORIES",
    "LEGACY_METADATA_FIELDS",
    "NEW_METADATA_FIELDS",
    "PROFILE_DISPLAY_KEYS",
    "RELATION_TYPES",
    "drop_deleted_scene_entries",
    "entry_key",
    "format_entry_triplet",
    "is_scene_type_id",
    "normalize_new_metadata",
    "project_domain_names",
    "project_l0_profile",
    "project_metadata",
    "scoped_metadata",
    "split_metadata_entry",
]