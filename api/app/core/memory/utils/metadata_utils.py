"""
Metadata utility functions for cleaning, validating, aggregating, and merging
user metadata extracted from conversations.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.memory.models.metadata_models import UserMetadata

logger = logging.getLogger(__name__)


def clean_metadata(raw: dict) -> dict:
    """
    Clean metadata by removing empty string values and empty array fields recursively.
    Only keeps fields with actual content. If a nested dict becomes empty after cleaning,
    it is removed too.
    """
    cleaned = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            nested = clean_metadata(value)
            if nested:
                cleaned[key] = nested
        elif isinstance(value, list):
            if len(value) > 0:
                cleaned[key] = value
        elif isinstance(value, str):
            if value != "":
                cleaned[key] = value
        else:
            cleaned[key] = value
    return cleaned

# TODO 这个函数没有调用的地方
def validate_metadata(raw: dict) -> Optional[UserMetadata]:
    """
    Validate metadata structure using the Pydantic UserMetadata model.
    Returns None and logs a WARNING on validation failure.
    """
    try:
        return UserMetadata.model_validate(raw)
    except Exception as e:
        logger.warning("Metadata validation failed: %s", e)
        return None


def merge_metadata(existing: dict, new: dict) -> dict:
    """
    Merge new extracted metadata with existing database metadata.
    - Scalar fields: new value overwrites old value
    - Array fields: support _op marker (append/replace/remove)
    - Missing top-level keys in new: preserve existing data
    - Auto-update _updated_at timestamp dict with field paths and ISO timestamps
    - When existing is None or {}: directly write new + _updated_at (no merge logic)
    """
    now = datetime.now(timezone.utc).isoformat()

    if not existing:
        # Direct write: new + _updated_at for all fields
        result = dict(new)
        updated_at = {}
        _collect_field_paths(result, "", updated_at, now)
        if updated_at:
            result["_updated_at"] = updated_at
        return result

    result = dict(existing)
    updated_at: dict = dict(result.get("_updated_at", {}))

    for key, new_value in new.items():
        if key == "_updated_at":
            continue

        old_value = result.get(key)

        if isinstance(new_value, dict) and isinstance(old_value, dict):
            # Nested dict merge (e.g. profile, behavioral_hints)
            _merge_nested(result, key, old_value, new_value, updated_at, now)
        elif isinstance(new_value, list) or (isinstance(new_value, dict) and "_op" in new_value):
            # Array field with possible _op
            _merge_array_field(result, key, old_value, new_value, updated_at, now)
        else:
            # Scalar top-level field
            if old_value != new_value:
                result[key] = new_value
                updated_at[key] = now
            # If equal, no change needed

    result["_updated_at"] = updated_at
    return result

# TODO 考虑大函数包含小函数，因为只服务于大函数，实现代码文件的结构清楚
def _collect_field_paths(data: dict, prefix: str, updated_at: dict, now: str) -> None:
    """Collect all leaf field paths for _updated_at on direct write."""
    for key, value in data.items():
        if key == "_updated_at":
            continue
        path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            _collect_field_paths(value, path, updated_at, now)
        else:
            updated_at[path] = now


def _merge_nested(
    result: dict, key: str, old_dict: dict, new_dict: dict,
    updated_at: dict, now: str
) -> None:
    """Merge a nested dict (e.g. profile, behavioral_hints)."""
    merged = dict(old_dict)
    for field, new_val in new_dict.items():
        old_val = merged.get(field)
        path = f"{key}.{field}"

        if isinstance(new_val, list) or (isinstance(new_val, dict) and "_op" in new_val):
            _merge_array_field_inner(merged, field, old_val, new_val, updated_at, path, now)
        else:
            # Scalar field
            if old_val != new_val:
                merged[field] = new_val
                updated_at[path] = now
    result[key] = merged


def _merge_array_field(
    result: dict, key: str, old_value, new_value,
    updated_at: dict, now: str
) -> None:
    """Merge a top-level array field with _op support."""
    _merge_array_field_inner(result, key, old_value, new_value, updated_at, key, now)


def _merge_array_field_inner(
    container: dict, field: str, old_value, new_value,
    updated_at: dict, path: str, now: str
) -> None:
    """Core array merge logic with _op support."""
    # Determine op and items
    if isinstance(new_value, dict) and "_op" in new_value:
        op = new_value.get("_op", "append")
        items = new_value.get(field, new_value.get("items", []))
        # If the dict has a key matching the field name, use it; otherwise look for list values
        if not isinstance(items, list):
            # Try to find the list value in the dict (excluding _op)
            for k, v in new_value.items():
                if k != "_op" and isinstance(v, list):
                    items = v
                    break
            else:
                items = []
    elif isinstance(new_value, list):
        op = "append"
        items = new_value
    else:
        op = "append"
        items = []

    old_arr = old_value if isinstance(old_value, list) else []

    if op == "replace":
        new_arr = items
    elif op == "remove":
        new_arr = [x for x in old_arr if x not in items]
    else:
        # append (default): merge and deduplicate
        seen = list(old_arr)
        for item in items:
            if item not in seen:
                seen.append(item)
        new_arr = seen

    if old_arr != new_arr:
        container[field] = new_arr
        updated_at[path] = now
    else:
        container[field] = new_arr
