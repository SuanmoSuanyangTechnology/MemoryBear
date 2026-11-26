"""Merge one user's metadata into another's during an end-user merge.

A trimmed port of the API's ``metadata_models.merge_user_meta_data`` and the
helpers it needs. The rules are the merge contract, not display logic:

- ``identity_background`` / ``relationships``: union of entries, de-duplicated
  by the normalised ``a | b | c`` key;
- ``domain_specific_info``: union per scene key; when a scene-name → id map is
  supplied, both sides are remapped first so a name and an id never split one
  bucket;
- legacy eight fields: historical single values and lists both survive,
  case-insensitive de-duplication;
- other list fields: case-insensitive union (historical behaviour);
- a side with the wrong type is ignored with a warning.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

#: 历史旧 8 字段：停止生成，读取和合并继续保留。
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


def _entry_key(item: str) -> str:
    """条目比对键：``a|b|c`` 与 ``a | b | c`` 视为同一条目。"""

    parts = [p.strip() for p in (item or "").strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    return "|".join(parts)


def _legacy_metadata_entry_key(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return "str", value.lower()
    return type(value).__name__, json.dumps(value, sort_keys=True, ensure_ascii=False)


def normalize_legacy_metadata_values(value: Any) -> list[Any]:
    """历史字段按列表读取；单值整体保留，空值不生成条目。"""

    values = value if isinstance(value, list) else [value]
    return [item for item in values if item is not None and item != ""]


def _union_entries(base: list[Any], extra: list[Any]) -> list[Any]:
    """字符串按 ``_entry_key`` 去重；非字符串按类型和值去重，原样保留。"""

    result = list(base)
    seen = {_entry_key(x) for x in result if isinstance(x, str)}
    seen_non_strings = {
        _legacy_metadata_entry_key(x) for x in result if not isinstance(x, str)
    }
    for item in extra:
        if not isinstance(item, str):
            non_string_key = _legacy_metadata_entry_key(item)
            if non_string_key not in seen_non_strings:
                seen_non_strings.add(non_string_key)
                result.append(item)
            continue
        key = _entry_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _remap_domain_keys(
    domains: Any, key_map: dict[str, str], *, preserve_unmapped: bool
) -> dict[str, list[Any]]:
    if not isinstance(domains, dict):
        return {}
    destinations = set(key_map.values())
    remapped: dict[str, list[Any]] = {}
    for key, items in domains.items():
        if not isinstance(items, list):
            logger.warning("[Metadata] 场景换键忽略非列表数据: %s", key)
            continue
        destination = key_map.get(key, key)
        if preserve_unmapped and key not in key_map and key not in destinations:
            remapped[destination] = list(items)
        else:
            remapped[destination] = _union_entries(remapped.get(destination, []), items)
    return remapped


def is_scene_type_id(key: object) -> bool:
    if not isinstance(key, str):
        return False
    try:
        UUID(key)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def normalize_domain_ids(
    domain_specific_info: Any,
    name_to_id_map: dict[str, str] | None = None,
    *,
    preserve_unmapped: bool = False,
) -> dict[str, list[Any]]:
    """存储侧名称转 ID；已知 ID 优先保留，同一 ID 下条目合并去重。"""

    mapping = name_to_id_map or {}
    known_ids = set(mapping.values())
    return _remap_domain_keys(
        domain_specific_info,
        {name: scene_id for name, scene_id in mapping.items() if name not in known_ids},
        preserve_unmapped=preserve_unmapped,
    )


def merge_user_meta_data(
    target: dict[str, Any] | None,
    source: dict[str, Any] | None,
    name_to_id_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """用户归并时把 ``source`` 的 metadata 合并进 ``target``，返回新字典。"""

    merged: dict[str, Any] = dict(target or {})
    source_meta: dict[str, Any] = dict(source or {})
    if name_to_id_map:
        for metadata in (merged, source_meta):
            domains = metadata.get("domain_specific_info")
            if isinstance(domains, dict):
                metadata["domain_specific_info"] = normalize_domain_ids(
                    domains, name_to_id_map
                )

    for key, values in source_meta.items():
        if key in LEGACY_METADATA_FIELDS:
            items = normalize_legacy_metadata_values(merged.get(key))
            items += normalize_legacy_metadata_values(values)
            if items:
                unique: dict[tuple[str, str], Any] = {}
                for item in items:
                    unique.setdefault(_legacy_metadata_entry_key(item), item)
                merged[key] = list(unique.values())
        elif key in ("identity_background", "relationships"):
            if not isinstance(values, list):
                logger.warning(
                    "[Metadata] 归并忽略 source 中类型不符的字段: %s=%s",
                    key,
                    type(values).__name__,
                )
                continue
            existing = merged.get(key)
            if existing is not None and not isinstance(existing, list):
                logger.warning(
                    "[Metadata] 归并忽略 target 中类型不符的字段: %s=%s",
                    key,
                    type(existing).__name__,
                )
                existing = None
            merged[key] = _union_entries(existing or [], values)
        elif key == "domain_specific_info":
            if not isinstance(values, dict):
                logger.warning(
                    "[Metadata] 归并忽略 source 中类型不符的字段: %s=%s",
                    key,
                    type(values).__name__,
                )
                continue
            existing = merged.get(key)
            if existing is not None and not isinstance(existing, dict):
                logger.warning(
                    "[Metadata] 归并忽略 target 中类型不符的字段: %s=%s",
                    key,
                    type(existing).__name__,
                )
                existing = None
            scenes: dict[str, Any] = dict(existing or {})
            for scene_key, scene_items in values.items():
                if not isinstance(scene_items, list):
                    logger.warning(
                        "[Metadata] 归并忽略 source 场景中类型不符的数据: %s", scene_key
                    )
                    continue
                current = scenes.get(scene_key)
                if current is not None and not isinstance(current, list):
                    logger.warning(
                        "[Metadata] 归并忽略 target 场景中类型不符的数据: %s", scene_key
                    )
                    current = None
                scenes[scene_key] = _union_entries(current or [], scene_items)
            merged[key] = scenes
        elif isinstance(values, list):
            existing_list = list(merged.get(key) or [])
            existing_set = {str(v).lower() for v in existing_list}
            for value in values:
                if str(value).lower() not in existing_set:
                    existing_list.append(value)
                    existing_set.add(str(value).lower())
            merged[key] = existing_list
    return merged


__all__ = [
    "LEGACY_METADATA_FIELDS",
    "is_scene_type_id",
    "merge_user_meta_data",
    "normalize_domain_ids",
    "normalize_legacy_metadata_values",
]