"""Pure metadata migration policy shared by API reflection and memory-jobs."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

MIGRATION_CUTOFF = datetime(2026, 11, 27)
METADATA_MIGRATION_CIRCUIT_BREAKER_KEY_FMT = (
    "metadata_migration:circuit_breaker:{workspace_id}"
)
LEGACY_INPUT_FIELDS = ("core_facts", "relations", "beliefs_or_stances", "anchors", "events")
NEW_FIELDS = ("identity_background", "relationships", "domain_specific_info")


def metadata_migration_circuit_breaker_key(workspace_id: str) -> str:
    """Return the shared Redis key used to pause a workspace after model failure."""

    return METADATA_MIGRATION_CIRCUIT_BREAKER_KEY_FMT.format(workspace_id=workspace_id)


def legacy_metadata_input(metadata: Mapping[str, object]) -> dict[str, object]:
    """Select only populated fields permitted in the migration prompt."""
    return {field: metadata[field] for field in LEGACY_INPUT_FIELDS if metadata.get(field)}


def apply_migration_response_operations(
    existing: object,
    operations: Sequence[Mapping[str, object]],
    response_entity_id: str,
    expected_entity_id: str,
) -> tuple[dict[str, object], int, int]:
    """Apply a parsed model response after checking its user identity."""
    if response_entity_id != expected_entity_id:
        raise ValueError("migration response does not contain the requested user")
    return apply_migration_operations(existing, operations)


def metadata_write_column(created_at: datetime | None, meta_data_new: object) -> str:
    """NULL creation time denotes an old user; an empty new object is migrated."""
    if meta_data_new is not None:
        return "meta_data_new"
    if created_at is not None:
        created_at = (
            created_at.astimezone(UTC).replace(tzinfo=None)
            if created_at.tzinfo is not None
            else created_at
        )
        if created_at >= MIGRATION_CUTOFF:
            return "meta_data_new"
    return "meta_data"


def new_metadata(value: object) -> dict[str, object]:
    """Copy existing new keys, preserving domains without schema filtering."""
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be an object")
    result = {key: copy.deepcopy(value[key]) for key in NEW_FIELDS if key in value}
    for key in NEW_FIELDS:
        expected = dict if key == "domain_specific_info" else list
        if key in result and not isinstance(result[key], expected):
            raise ValueError(f"invalid metadata field: {key}")
    return result


_IDENTITY_SLOTS = {
    "name": {"full_name", "preferred_alias"},
    "age": {"age_or_age_range", "date_of_birth"},
    "from": {"birthplace", "native_place"},
    "residence": {""},
    "education": {""},
    "occupation": {""},
    "family": {
        "living_status",
        "marital_status",
        "father",
        "mother",
        "partner",
        "child",
        "sibling",
        "relative",
    },
    "religion_or_politics": {"religion", "politics"},
    "health": {"physical", "mental"},
}


def _valid_entry(field: str, value: str) -> bool:
    parts = [part.strip() for part in value.split("|", 2)]
    if len(parts) != 3 or not parts[2]:
        return False
    category, slot, _ = parts
    if field == "identity_background":
        return slot in _IDENTITY_SLOTS.get(category, set())
    return category in {"person", "group", "object", "pet"} and not slot


def apply_migration_operations(
    existing: object,
    operations: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], int, int]:
    """Only add to the two basic keys. Never generate or prune domain data."""
    merged = new_metadata(existing)
    applied = rejected = valid = 0
    for operation in operations:
        field, value = operation.get("field"), operation.get("value")
        if (
            operation.get("op") != "add"
            or field not in ("identity_background", "relationships")
            or not isinstance(value, str)
            or not _valid_entry(field, value)
        ):
            rejected += 1
            continue
        valid += 1
        values = merged.setdefault(field, [])
        value = value.strip()
        if value in values:
            continue
        # Existing facts win for singleton slots; do not overwrite them with
        # a historical fact for which no reliable recency evidence exists.
        slot = tuple(part.strip() for part in value.split("|", 2)[:2])
        singleton = slot in {
            ("name", "full_name"),
            ("age", "date_of_birth"),
            ("age", "age_or_age_range"),
            ("from", "birthplace"),
            ("from", "native_place"),
            ("family", "marital_status"),
        }
        if singleton and any(
            isinstance(item, str) and tuple(part.strip() for part in item.split("|", 2)[:2]) == slot
            for item in values
        ):
            continue
        values.append(value)
        applied += 1
    if rejected and not valid:
        raise ValueError("migration output contains no valid applied operation")
    return merged, applied, rejected


def metadata_read_expression(meta_data, meta_data_new):
    """SQL NULL routing shared by all independent consumer repositories.

    SQLAlchemy stays an optional lazy import: transport-only SDK users do not
    need to install it. JSONB {} is deliberately not a fallback condition.
    """
    from sqlalchemy import case

    return case((meta_data_new.is_(None), meta_data), else_=meta_data_new).label("meta_data")
