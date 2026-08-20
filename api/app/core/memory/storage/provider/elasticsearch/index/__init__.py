import asyncio
from collections.abc import Mapping
from copy import deepcopy
from enum import StrEnum
from typing import Any
from uuid import uuid4

from elasticsearch import AsyncElasticsearch
from elastic_transport import ObjectApiResponse

from app.aioRedis import get_thread_safe_redis
from app.core.memory.storage.enums import MemoryNodeLabel
from app.core.memory.storage.provider.elasticsearch.index.definitions import (
    INDEX_ALIAS_SUFFIX,
    INDEX_DEFINITIONS,
    INDEX_SHARD_COUNT,
    IndexDefinition,
    get_index_definition,
    get_index_name,
    validate_definition_registry,
)
from .migration_lock import (
    AsyncRedisClient,
    RedisMigrationLease,
)

__all__ = [
    "INDEX_ALIAS_SUFFIX",
    "INDEX_DEFINITIONS",
    "INDEX_SCHEMA_META_KEY",
    "INDEX_SHARD_COUNT",
    "IndexDefinition",
    "ensure_index",
    "ensure_indices",
    "get_index_definition",
    "get_index_name",
    "validate_index",
]

INDEX_SCHEMA_META_KEY = "redbear_memory_storage"
MIGRATION_WAIT_TIMEOUT_SECONDS = 300.0
MIGRATION_POLL_INTERVAL_SECONDS = 0.5


class IndexUpdateAction(StrEnum):
    CURRENT = "current"
    ADDITIVE_MAPPING = "additive_mapping"
    REINDEX = "reindex"


def _contains_definition(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(
            (
                key in actual
                and _contains_definition(actual[key], value)
            )
            or (key not in actual and value in ({}, []))
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(_contains_definition(item, required) for item in actual)
            for required in expected
        )
    return actual == expected


def _canonical_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def visit(value: Any, prefix: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                visit(child, child_prefix)
            return
        canonical_key = prefix.removeprefix("index.")
        flattened[canonical_key] = value

    visit(settings, "")
    return flattened


def _settings_contain_definition(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    actual_flat = _canonical_settings(actual)
    expected_flat = _canonical_settings(expected)
    return all(
        key in actual_flat
        and str(actual_flat[key]).lower() == str(value).lower()
        for key, value in expected_flat.items()
    )


def _versioned_mappings(
    label: MemoryNodeLabel,
    definition: IndexDefinition,
) -> dict[str, Any]:
    mappings = deepcopy(definition.mappings)
    metadata = mappings.setdefault("_meta", {})
    if not isinstance(metadata, dict):
        raise RuntimeError(
            f"Elasticsearch index definition '{definition.name}' _meta must be a mapping"
        )
    metadata[INDEX_SCHEMA_META_KEY] = {
        "label": label.name,
        "schema_version": definition.schema_version,
        "generation": definition.generation,
    }
    return mappings


def _new_physical_index_name(definition: IndexDefinition) -> str:
    return f"{definition.name}_g{definition.generation}_{uuid4().hex[:12]}"


def _single_index_payload(
    response: ObjectApiResponse[Any],
    requested_name: str,
    operation: str,
) -> tuple[str, Mapping[str, Any]]:
    if requested_name in response:
        payload = response[requested_name]
        if isinstance(payload, Mapping):
            return requested_name, payload
    items = [
        (name, payload)
        for name, payload in response.items()
        if isinstance(payload, Mapping)
    ]
    if len(items) != 1:
        raise RuntimeError(
            f"Elasticsearch {operation} for '{requested_name}' returned "
            f"{len(items)} indices"
        )
    return items[0]


async def _get_alias_target(
    client: AsyncElasticsearch,
    alias: str,
) -> str | None:
    if not await client.indices.exists_alias(name=alias):
        return None
    response = await client.indices.get_alias(name=alias)
    targets = list(response)
    if len(targets) != 1:
        raise RuntimeError(
            f"Elasticsearch alias '{alias}' must resolve to exactly one index; "
            f"found {len(targets)}"
        )
    return str(targets[0])


async def _get_mappings(
    client: AsyncElasticsearch,
    index_name: str,
) -> tuple[str, Mapping[str, Any]]:
    response = await client.indices.get_mapping(index=index_name)
    physical_name, payload = _single_index_payload(
        response, index_name, "get_mapping"
    )
    mappings = payload.get("mappings", {})
    if not isinstance(mappings, Mapping):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' returned invalid mappings"
        )
    return physical_name, mappings


def _schema_identity(
    mappings: Mapping[str, Any],
) -> tuple[str, int, int] | None:
    metadata = mappings.get("_meta", {})
    if not isinstance(metadata, Mapping):
        return None
    storage_metadata = metadata.get(INDEX_SCHEMA_META_KEY, {})
    if not isinstance(storage_metadata, Mapping):
        return None
    label = storage_metadata.get("label")
    if not isinstance(label, str):
        return None

    schema_version = storage_metadata.get("schema_version")
    generation = storage_metadata.get("generation")
    if schema_version is None and generation is None:
        legacy_version = storage_metadata.get("version")
        if (
            isinstance(legacy_version, int)
            and not isinstance(legacy_version, bool)
            and legacy_version > 0
        ):
            return label, legacy_version, legacy_version
        return None
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or schema_version < 1
        or generation < 1
    ):
        return None
    return label, schema_version, generation


async def validate_index(
    client: AsyncElasticsearch,
    label: MemoryNodeLabel,
    index_name: str | None = None,
) -> str:
    definition = get_index_definition(label)
    requested_name = index_name or definition.alias
    settings_response = await client.indices.get_settings(index=requested_name)
    physical_name, settings_payload = _single_index_payload(
        settings_response, requested_name, "get_settings"
    )
    index_settings = settings_payload.get("settings", {})
    shard_count = (
        index_settings.get("index", {}).get("number_of_shards")
        if isinstance(index_settings, Mapping)
        else None
    )
    expected_shards = definition.settings.get("number_of_shards")
    if str(shard_count) != str(expected_shards):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' must have "
            f"{str(expected_shards)} primary shards, found {str(shard_count)}; "
            "increment generation to rebuild it"
        )
    if not _settings_contain_definition(index_settings, definition.settings):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' does not contain its "
            "configured settings; increment generation to rebuild it"
        )

    mapped_name, mappings = await _get_mappings(client, requested_name)
    if mapped_name != physical_name:
        raise RuntimeError(
            f"Elasticsearch settings and mappings resolved to different indices "
            f"for '{requested_name}'"
        )
    expected_identity = (
        label.name,
        definition.schema_version,
        definition.generation,
    )
    actual_identity = _schema_identity(mappings)
    if actual_identity != expected_identity:
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' has schema identity "
            f"{str(actual_identity)}, expected {str(expected_identity)}"
        )
    if not _contains_definition(mappings, definition.mappings):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' does not contain its "
            "configured mapping; increment schema_version for additive fields "
            "or generation for incompatible changes"
        )
    return physical_name


def _required_nonnegative_int(
    result: ObjectApiResponse[Any],
    field: str,
    operation: str,
) -> int:
    value = result.get(field)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise RuntimeError(
            f"Elasticsearch {operation} returned invalid {field}: {value!r}"
        )
    return value


def _validate_reindex_result(result: ObjectApiResponse[Any]) -> None:
    if result.get("timed_out"):
        raise RuntimeError("Elasticsearch reindex timed out")
    failures = result.get("failures") or []
    if failures:
        raise RuntimeError(f"Elasticsearch reindex failures: {failures!r}")
    version_conflicts = _required_nonnegative_int(
        result, "version_conflicts", "reindex"
    )
    if version_conflicts:
        raise RuntimeError(
            f"Elasticsearch reindex had {version_conflicts} version conflicts"
        )
    total = _required_nonnegative_int(result, "total", "reindex")
    completed = sum(
        _required_nonnegative_int(result, field, "reindex")
        for field in ("created", "updated", "noops")
    )
    if completed != total:
        raise RuntimeError(
            f"Elasticsearch reindex copied {completed} of {total} documents"
        )


def _count_from_response(
    result: ObjectApiResponse[Any],
    index_name: str,
) -> int:
    return _required_nonnegative_int(
        result,
        "count",
        f"count for '{index_name}'",
    )


async def _copy_index(
    client: AsyncElasticsearch,
    source_index: str,
    destination_index: str,
) -> None:
    result = await client.reindex(
        source={"index": source_index},
        dest={"index": destination_index},
        conflicts="abort",
        refresh=True,
        wait_for_completion=True,
    )
    _validate_reindex_result(result)

    source_count = _count_from_response(
        await client.count(index=source_index), source_index
    )
    destination_count = _count_from_response(
        await client.count(index=destination_index), destination_index
    )
    if source_count != destination_count:
        raise RuntimeError(
            f"Elasticsearch reindex count mismatch: source={source_count}, "
            f"destination={destination_count}"
        )


async def _switch_alias(
    client: AsyncElasticsearch,
    alias: str,
    destination_index: str,
    current_index: str | None,
) -> None:
    actions: list[dict[str, Any]] = []
    if current_index is not None:
        actions.append(
            {"remove": {"index": current_index, "alias": alias}}
        )
    actions.append(
        {
            "add": {
                "index": destination_index,
                "alias": alias,
                "is_write_index": True,
            }
        }
    )
    result = await client.indices.update_aliases(actions=actions)
    if result.get("acknowledged") is not True:
        raise RuntimeError(
            f"Elasticsearch alias switch for '{alias}' was not acknowledged"
        )


async def _set_write_block(
    client: AsyncElasticsearch,
    index_name: str,
    blocked: bool,
) -> None:
    result = await client.indices.put_settings(
        index=index_name,
        settings={"index.blocks.write": blocked},
    )
    if result.get("acknowledged") is not True:
        state = "enable" if blocked else "disable"
        raise RuntimeError(
            f"Elasticsearch failed to {state} write block for '{index_name}'"
        )


def _legacy_index_name(definition: IndexDefinition) -> str:
    """Return the pre-alias physical index name used by older releases."""
    return definition.name


async def _get_update_action(
    client: AsyncElasticsearch,
    label: MemoryNodeLabel,
    current_index: str,
) -> IndexUpdateAction:
    definition = get_index_definition(label)
    _, current_mappings = await _get_mappings(client, current_index)
    current_identity = _schema_identity(current_mappings)
    if current_identity is None:
        raise RuntimeError(
            f"Elasticsearch alias '{definition.alias}' points to index "
            f"'{current_index}' without valid schema identity metadata; "
            "refusing automatic update"
        )
    current_label, current_schema_version, current_generation = current_identity
    if current_label != label.name:
        raise RuntimeError(
            f"Elasticsearch alias '{definition.alias}' points to label "
            f"{current_label!r}, expected {label.name!r}"
        )
    if current_generation > definition.generation:
        raise RuntimeError(
            f"Elasticsearch index '{current_index}' has generation "
            f"{current_generation}, newer than configured generation "
            f"{definition.generation}; refusing automatic downgrade"
        )
    if current_schema_version > definition.schema_version:
        raise RuntimeError(
            f"Elasticsearch index '{current_index}' has schema version "
            f"{current_schema_version}, newer than configured schema version "
            f"{definition.schema_version}; refusing automatic downgrade"
        )
    if current_generation < definition.generation:
        return IndexUpdateAction.REINDEX
    if current_schema_version < definition.schema_version:
        return IndexUpdateAction.ADDITIVE_MAPPING

    await validate_index(client, label, current_index)
    return IndexUpdateAction.CURRENT


async def _wait_for_migration(
    client: AsyncElasticsearch,
    redis_client: AsyncRedisClient,
    label: MemoryNodeLabel,
    lease: RedisMigrationLease,
    deadline: float,
) -> bool:
    """Wait for another owner, returning False when its lease disappears."""
    while True:
        current_index = await _get_alias_target(client, lease.alias)
        if (
            current_index is not None
            and await _get_update_action(client, label, current_index)
            is IndexUpdateAction.CURRENT
        ):
            return True

        if await redis_client.get(lease.key) is None:
            return False

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RuntimeError(
                f"Timed out waiting for Elasticsearch index migration "
                f"'{lease.alias}'"
            )
        await asyncio.sleep(min(MIGRATION_POLL_INTERVAL_SECONDS, remaining))


def _additive_properties_patch(
    current_mappings: Mapping[str, Any],
    definition: IndexDefinition,
) -> dict[str, Mapping[str, Any]]:
    desired_non_properties = {
        key: value
        for key, value in definition.mappings.items()
        if key not in {"properties", "_meta"}
    }
    if not _contains_definition(current_mappings, desired_non_properties):
        raise RuntimeError(
            f"Elasticsearch index '{definition.alias}' has non-additive "
            "mapping changes; increment generation to reindex"
        )

    current_properties = current_mappings.get("properties", {})
    desired_properties = definition.mappings.get("properties", {})
    if not isinstance(current_properties, Mapping) or not isinstance(
        desired_properties, Mapping
    ):
        raise RuntimeError(
            f"Elasticsearch index definition '{definition.name}' properties "
            "must be mappings"
        )

    patch: dict[str, Mapping[str, Any]] = {}
    for field, desired_mapping in desired_properties.items():
        if not isinstance(desired_mapping, Mapping):
            raise RuntimeError(
                f"Elasticsearch mapping for field '{field}' must be a mapping"
            )
        current_mapping = current_properties.get(field)
        if current_mapping is None:
            patch[str(field)] = desired_mapping
        elif not _contains_definition(current_mapping, desired_mapping):
            raise RuntimeError(
                f"Elasticsearch field '{field}' has an incompatible mapping "
                "change; increment generation to reindex"
            )
    return patch


async def _apply_additive_mapping(
    client: AsyncElasticsearch,
    label: MemoryNodeLabel,
    current_index: str,
    lease: RedisMigrationLease,
) -> bool:
    definition = get_index_definition(label)
    await lease.renew_now()

    settings_response = await client.indices.get_settings(index=current_index)
    physical_name, settings_payload = _single_index_payload(
        settings_response, current_index, "get_settings"
    )
    index_settings = settings_payload.get("settings", {})
    shard_count = (
        index_settings.get("index", {}).get("number_of_shards")
        if isinstance(index_settings, Mapping)
        else None
    )
    expected_shards = definition.settings.get("number_of_shards")
    if str(shard_count) != str(expected_shards):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' has incompatible settings; "
            "increment generation to reindex"
        )
    if not _settings_contain_definition(index_settings, definition.settings):
        raise RuntimeError(
            f"Elasticsearch index '{physical_name}' has incompatible settings; "
            "increment generation to reindex"
        )

    mapped_name, current_mappings = await _get_mappings(
        client, current_index
    )
    if mapped_name != physical_name:
        raise RuntimeError(
            f"Elasticsearch settings and mappings resolved to different indices "
            f"for '{current_index}'"
        )
    properties_patch = _additive_properties_patch(
        current_mappings, definition
    )
    versioned_mappings = _versioned_mappings(label, definition)
    metadata = versioned_mappings.get("_meta", {})
    if not isinstance(metadata, Mapping):
        raise RuntimeError("Elasticsearch generated _meta must be a mapping")

    await lease.renew_now()
    result = await client.indices.put_mapping(
        index=current_index,
        properties=properties_patch or None,
        meta=metadata,
    )
    if result.get("acknowledged") is not True:
        raise RuntimeError(
            f"Elasticsearch additive mapping update for '{current_index}' "
            "was not acknowledged"
        )
    await lease.ensure_owned()
    await validate_index(client, label, current_index)
    return True


async def _run_migration(
    client: AsyncElasticsearch,
    label: MemoryNodeLabel,
    current_index: str | None,
    lease: RedisMigrationLease,
) -> bool:
    definition = get_index_definition(label)
    await lease.renew_now()

    source_index = current_index
    legacy_index = _legacy_index_name(definition)
    if source_index is None and await client.indices.exists(index=legacy_index):
        source_index = legacy_index

    destination_index = _new_physical_index_name(definition)
    await client.indices.create(
        index=destination_index,
        settings=definition.settings,
        mappings=_versioned_mappings(label, definition),
    )
    await validate_index(client, label, destination_index)

    write_block_attempted = False
    migration_error: BaseException | None = None
    try:
        if source_index is not None:
            write_block_attempted = True
            await _set_write_block(client, source_index, True)
            refresh_result = await client.indices.refresh(index=source_index)
            shards = refresh_result.get("_shards", {})
            if (
                isinstance(shards, Mapping)
                and int(shards.get("failed", 0) or 0) > 0
            ):
                raise RuntimeError(
                    f"Elasticsearch refresh for '{source_index}' had "
                    f"shard failures: {shards.get('failures', [])!r}"
                )
            await _copy_index(client, source_index, destination_index)

        await lease.renew_now()
        latest_index = await _get_alias_target(client, definition.alias)
        if latest_index != current_index:
            raise RuntimeError(
                f"Elasticsearch alias '{definition.alias}' changed from "
                f"{current_index!r} to {latest_index!r} during migration; "
                "refusing stale alias switch"
            )

        await lease.renew_now()
        await _switch_alias(
            client,
            definition.alias,
            destination_index,
            current_index,
        )
        await lease.ensure_owned()
        if await _get_alias_target(client, definition.alias) != destination_index:
            raise RuntimeError(
                f"Elasticsearch alias '{definition.alias}' did not switch "
                f"to '{destination_index}'"
            )
    except BaseException as exc:
        migration_error = exc
        raise
    finally:
        if write_block_attempted and source_index is not None:
            try:
                await _set_write_block(client, source_index, False)
            except Exception as unblock_error:
                if migration_error is None:
                    raise
                migration_error.add_note(
                    f"Additionally failed to remove write block from "
                    f"'{source_index}': {unblock_error!r}"
                )
    return True


async def ensure_index(
    client: AsyncElasticsearch,
    label: MemoryNodeLabel,
    redis_client: AsyncRedisClient | None = None,
) -> bool:
    """Create, update, migrate, or wait for one label's target index."""
    definition = get_index_definition(label)
    current_index = await _get_alias_target(client, definition.alias)
    action = (
        IndexUpdateAction.REINDEX
        if current_index is None
        else await _get_update_action(client, label, current_index)
    )
    if action is IndexUpdateAction.CURRENT:
        return False

    redis_client = redis_client or get_thread_safe_redis()
    wait_deadline = (
        asyncio.get_running_loop().time() + MIGRATION_WAIT_TIMEOUT_SECONDS
    )
    while True:
        lease = RedisMigrationLease(redis_client, definition.alias)
        if not await lease.acquire():
            if await _wait_for_migration(
                client,
                redis_client,
                label,
                lease,
                wait_deadline,
            ):
                return False
            continue

        body_error: BaseException | None = None
        try:
            current_index = await _get_alias_target(
                client, definition.alias
            )
            action = (
                IndexUpdateAction.REINDEX
                if current_index is None
                else await _get_update_action(client, label, current_index)
            )
            if action is IndexUpdateAction.CURRENT:
                return False
            if action is IndexUpdateAction.ADDITIVE_MAPPING:
                if current_index is None:
                    raise RuntimeError(
                        "Additive mapping update requires an existing index"
                    )
                return await _apply_additive_mapping(
                    client,
                    label,
                    current_index,
                    lease,
                )
            return await _run_migration(
                client,
                label,
                current_index,
                lease,
            )
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            try:
                released = await lease.release()
                if not released:
                    raise RuntimeError(
                        f"Redis migration lock ownership lost before release: "
                        f"{lease.key}"
                    )
            except Exception as release_error:
                if body_error is None:
                    raise
                body_error.add_note(
                    f"Additionally failed to release Redis migration lock "
                    f"'{lease.key}': {release_error!r}"
                )

    raise RuntimeError("Elasticsearch index migration loop exited unexpectedly")


async def ensure_indices(
    client: AsyncElasticsearch,
    redis_client: AsyncRedisClient | None = None,
) -> tuple[str, ...]:
    """Ensure every explicitly registered label has a current versioned index."""
    validate_definition_registry()
    changed: list[str] = []
    for label in INDEX_DEFINITIONS:
        if await ensure_index(client, label, redis_client):
            changed.append(get_index_name(label))
    return tuple(changed)
