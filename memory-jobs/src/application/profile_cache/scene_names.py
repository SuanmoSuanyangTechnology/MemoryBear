"""Scene-name projection for the insight input.

``domain_specific_info`` is stored keyed by ``scene_type_id`` (a UUID). The model
should see scene *names*, and entries whose scene type has been deleted must not
reach the prompt. Both rules are the API's, so the insight input stays identical
across services.

The lookup belongs to the enterprise edition; the caller passes it in, so this
module stays free of transport details and the open build simply passes ``None``.
Without a lookup every uuid-keyed scene counts as unknown and is dropped, while
legacy name-keyed entries pass through unchanged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from src.application.ports.scene_names import SceneNameLookup
from src.domain.shared.ids import TenantId

logger = logging.getLogger(__name__)


def is_scene_type_id(key: object) -> bool:
    """True when a ``domain_specific_info`` key is a ``scene_type_id`` (uuid)."""

    if not isinstance(key, str):
        return False
    try:
        UUID(key)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def drop_deleted_scene_entries(
    domain_specific_info: Any, scene_names_by_id: Mapping[str, str] | None = None
) -> Any:
    """Drop entries whose uuid scene key has no resolved name (scene deleted).

    Non-uuid keys (the legacy name-keyed format) pass through untouched.
    """

    if not isinstance(domain_specific_info, dict):
        return domain_specific_info
    names = scene_names_by_id or {}
    return {
        key: items
        for key, items in domain_specific_info.items()
        if not (is_scene_type_id(key) and key not in names)
    }


def _entry_key(item: str) -> str:
    """Comparison key: ``a|b|c`` and ``a | b | c`` are the same entry."""

    parts = [part.strip() for part in (item or "").strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    return "|".join(parts)


def _json_entry_key(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return "str", value.lower()
    return type(value).__name__, json.dumps(value, sort_keys=True, ensure_ascii=False)


def _union_entries(base: list[Any], extra: list[Any]) -> list[Any]:
    """Deduplicate strings by parsed entry key, other JSON values by type+value."""

    result = list(base)
    seen = {_entry_key(item) for item in result if isinstance(item, str)}
    seen_other = {_json_entry_key(item) for item in result if not isinstance(item, str)}
    for item in extra:
        if not isinstance(item, str):
            key = _json_entry_key(item)
            if key not in seen_other:
                seen_other.add(key)
                result.append(item)
            continue
        key = _entry_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def project_domain_names(
    domain_specific_info: Any, scene_names_by_id: Mapping[str, str] | None = None
) -> dict[str, list[Any]]:
    """Replace uuid scene keys with names, merging entries that collide."""

    if not isinstance(domain_specific_info, dict):
        return {}
    scene_names = scene_names_by_id or {}
    remapped: dict[str, list[Any]] = {}
    for key, items in domain_specific_info.items():
        if not isinstance(items, list):
            logger.warning("场景换键忽略非列表数据: %s", key)
            continue
        destination = scene_names.get(key, key)
        remapped[destination] = _union_entries(remapped.get(destination, []), items)
    return remapped


def load_scene_names(
    metadata: object,
    lookup: SceneNameLookup,
    tenant_id: TenantId | None,
) -> dict[str, str] | None:
    """Resolve the scene names the metadata's keys point at, scoped to a tenant.

    ``{}`` means "no uuid scene has a name": there is nothing to resolve, or
    the lookup (an adapter in a deployment without the enterprise service)
    answered authoritatively. The caller then drops the unknown uuid groups
    exactly like the API does.

    ``None`` means the lookup is unavailable (a failure or an adapter that
    reports ``None``) or the workspace has no tenant. The caller must then skip
    the projection and keep the stored keys: neither an outage nor a missing
    tenant is evidence that the scenes were deleted. Tenantless workspaces are
    rejected by model resolution before this runs, so that branch is defensive.
    """

    domains = metadata.get("domain_specific_info") if isinstance(metadata, dict) else None
    if not isinstance(domains, dict):
        return {}
    keyed_ids: dict[str, UUID] = {}
    for key in domains:
        if is_scene_type_id(key):
            keyed_ids[key] = UUID(key)
    if not keyed_ids:
        return {}
    if tenant_id is None:
        # Scenes are tenant-owned; without one there is nothing legitimate to
        # look up, and dropping the groups would be data loss.
        return None

    try:
        names = lookup.resolve(list({*keyed_ids.values()}), tenant_id)
    except Exception:
        logger.warning("场景名查询失败，保留存储原键", exc_info=True)
        return None
    if names is None:
        logger.warning(
            "场景名查询不可用，保留存储原键",
            extra={"event": "profile_scene_names_unavailable"},
        )
        return None
    return {
        key: names[str(value)]
        for key, value in keyed_ids.items()
        if str(value) in names
    }
