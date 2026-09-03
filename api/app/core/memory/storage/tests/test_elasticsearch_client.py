import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from elasticsearch import AsyncElasticsearch

from app.core.config import settings
from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.models import NodeFilter, NodeProjection, NodeSort
from app.core.memory.storage.models.dto import StorageItem
from app.core.memory.storage.provider.elasticsearch import index as elasticsearch_index
from app.core.memory.storage.provider.elasticsearch.client import (
    PIT_KEEP_ALIVE,
    SEARCH_BATCH_SIZE,
    ElasticClient,
)
from app.core.memory.storage.provider.elasticsearch.serialization import (
    normalize_elasticsearch_document,
)
from app.core.memory.storage.provider.elasticsearch.config import (
    build_elasticsearch_client_config,
)
from app.core.memory.storage.provider.elasticsearch.index import (
    INDEX_SCHEMA_META_KEY,
    ensure_index,
    ensure_indices,
)
from app.core.memory.storage.provider.elasticsearch.index.definitions import (
    INDEX_DEFINITIONS,
    INDEX_SHARD_COUNT,
    get_index_definition,
    get_index_name,
)
from app.core.memory.storage.provider.elasticsearch.index.migration_lock import (
    CHECK_LOCK_SCRIPT,
    RELEASE_LOCK_SCRIPT,
    RENEW_LOCK_SCRIPT,
    RedisMigrationLease,
)
from app.core.memory.storage.tests.elasticsearch_test_definitions import (
    TEST_INDEX_DEFINITION,
    TEST_INDEX_LABEL,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.eval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.eval_errors: dict[str, list[Exception]] = {}
        self.eval_results: dict[str, list[int]] = {}

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        self.set_calls.append(
            {"name": name, "value": value, "nx": nx, "px": px}
        )
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        self.get_calls.append(name)
        return self.values.get(name)

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> int:
        assert numkeys == 1
        self.eval_calls.append((script, keys_and_args))
        errors = self.eval_errors.get(script)
        if errors:
            raise errors.pop(0)
        results = self.eval_results.get(script)
        if results:
            return results.pop(0)
        key = str(keys_and_args[0])
        token = str(keys_and_args[1])
        if script == CHECK_LOCK_SCRIPT:
            return int(self.values.get(key) == token)
        if script == RENEW_LOCK_SCRIPT:
            return int(self.values.get(key) == token)
        if script == RELEASE_LOCK_SCRIPT:
            if self.values.get(key) != token:
                return 0
            del self.values[key]
            return 1
        raise AssertionError("unexpected Redis script")


@pytest.fixture(autouse=True)
def fake_migration_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakeRedis:
    assert TEST_INDEX_LABEL not in INDEX_DEFINITIONS
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        TEST_INDEX_LABEL,
        TEST_INDEX_DEFINITION,
    )
    fake = _FakeRedis()
    monkeypatch.setattr(
        elasticsearch_index,
        "get_thread_safe_redis",
        lambda: fake,
    )
    return fake


class _FakeIndices:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = set(existing or ())
        self.aliases: dict[str, str] = {}
        self.get_alias_results: dict[str, dict[str, Any]] = {}
        self.exists_calls: list[str] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_aliases_calls: list[list[dict[str, Any]]] = []
        self.put_mapping_calls: list[dict[str, Any]] = []
        self.put_settings_calls: list[dict[str, Any]] = []
        self.put_settings_errors: list[Exception | None] = []
        self.refresh_calls: list[str] = []
        self.write_blocks: dict[str, bool] = {}
        self.update_aliases_result: dict[str, Any] = {"acknowledged": True}
        definitions_by_name = {
            definition.name: definition
            for definition in INDEX_DEFINITIONS.values()
        }
        self.settings: dict[str, dict[str, Any]] = {
            name: dict(definitions_by_name[name].settings)
            for name in self.existing
        }
        self.mappings: dict[str, dict[str, Any]] = {
            name: dict(definitions_by_name[name].mappings)
            for name in self.existing
        }

    def resolve(self, index: str) -> str:
        return self.aliases.get(index, index)

    async def exists(self, *, index: str) -> bool:
        self.exists_calls.append(index)
        return index in self.existing or index in self.aliases

    async def exists_alias(self, *, name: str) -> bool:
        return name in self.aliases

    async def get_alias(self, *, name: str) -> dict[str, Any]:
        if name in self.get_alias_results:
            return self.get_alias_results[name]
        target = self.aliases[name]
        return {
            target: {
                "aliases": {name: {"is_write_index": True}},
            }
        }

    async def create(self, **kwargs: Any) -> dict[str, bool]:
        self.create_calls.append(kwargs)
        index_name = kwargs["index"]
        self.existing.add(index_name)
        self.settings[index_name] = kwargs["settings"]
        self.mappings[index_name] = kwargs["mappings"]
        return {"acknowledged": True}

    async def get_settings(self, *, index: str) -> dict[str, Any]:
        physical_name = self.resolve(index)
        return {
            physical_name: {
                "settings": {
                    "index": {
                        key: str(value)
                        for key, value in self.settings[physical_name].items()
                    }
                }
            }
        }

    async def get_mapping(self, *, index: str) -> dict[str, Any]:
        physical_name = self.resolve(index)
        return {physical_name: {"mappings": self.mappings[physical_name]}}

    async def put_mapping(self, **kwargs: Any) -> dict[str, bool]:
        self.put_mapping_calls.append(kwargs)
        physical_name = self.resolve(kwargs["index"])
        properties = kwargs.get("properties") or {}
        self.mappings[physical_name].setdefault("properties", {}).update(
            properties
        )
        if kwargs.get("meta") is not None:
            self.mappings[physical_name]["_meta"] = kwargs["meta"]
        return {"acknowledged": True}

    async def put_settings(
        self,
        *,
        index: str,
        settings: dict[str, Any],
    ) -> dict[str, bool]:
        self.put_settings_calls.append(
            {"index": index, "settings": settings}
        )
        self.write_blocks[index] = bool(settings["index.blocks.write"])
        if self.put_settings_errors:
            error = self.put_settings_errors.pop(0)
            if error is not None:
                raise error
        return {"acknowledged": True}

    async def refresh(self, *, index: str) -> dict[str, Any]:
        self.refresh_calls.append(index)
        return {"_shards": {"failed": 0}}

    async def update_aliases(
        self,
        *,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.update_aliases_calls.append(actions)
        if self.update_aliases_result.get("acknowledged") is not True:
            return self.update_aliases_result
        for action in actions:
            if "remove" in action:
                alias = action["remove"]["alias"]
                self.aliases.pop(alias, None)
            elif "add" in action:
                alias = action["add"]["alias"]
                self.aliases[alias] = action["add"]["index"]
        return self.update_aliases_result


class _FakeElasticsearch:
    def __init__(self, existing_indices: set[str] | None = None) -> None:
        self.indices = _FakeIndices(existing_indices)
        self.closed = False
        self.ping_calls = 0
        self.index_calls: list[dict[str, Any]] = []
        self.open_point_in_time_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.close_point_in_time_calls: list[dict[str, Any]] = []
        self.update_by_query_calls: list[dict[str, Any]] = []
        self.delete_by_query_calls: list[dict[str, Any]] = []
        self.reindex_calls: list[dict[str, Any]] = []
        self.document_counts: dict[str, int] = {
            name: 0 for name in self.indices.existing
        }
        self.reindex_result: dict[str, Any] | None = None
        self.count_results: list[dict[str, Any]] = []
        self.open_point_in_time_result: dict[str, Any] = {"id": "pit-1"}
        self.search_result: dict[str, Any] = {"hits": {"hits": []}}
        self.search_results: list[dict[str, Any]] = []
        self.index_result: dict[str, Any] = {"result": "created"}
        self.update_result: dict[str, Any] | None = None
        self.delete_result: dict[str, Any] | None = None
        self.updated = 2
        self.deleted = 3

    async def ping(self) -> bool:
        self.ping_calls += 1
        return True

    async def close(self) -> None:
        self.closed = True

    async def index(self, **kwargs: Any) -> dict[str, Any]:
        self.index_calls.append(kwargs)
        return self.index_result

    async def open_point_in_time(self, **kwargs: Any) -> dict[str, Any]:
        self.open_point_in_time_calls.append(kwargs)
        return self.open_point_in_time_result

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(kwargs)
        if self.search_results:

            return self.search_results.pop(0)
        return self.search_result

    async def close_point_in_time(self, **kwargs: Any) -> dict[str, bool]:
        self.close_point_in_time_calls.append(kwargs)
        return {"succeeded": True}

    async def update_by_query(self, **kwargs: Any) -> dict[str, Any]:
        self.update_by_query_calls.append(kwargs)
        if self.update_result is not None:
            return self.update_result
        return {"updated": self.updated}

    async def delete_by_query(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_by_query_calls.append(kwargs)
        if self.delete_result is not None:
            return self.delete_result
        return {"deleted": self.deleted}



    async def reindex(self, **kwargs: Any) -> dict[str, Any]:
        self.reindex_calls.append(kwargs)
        if self.reindex_result is not None:
            return self.reindex_result
        source = self.indices.resolve(kwargs["source"]["index"])
        destination = kwargs["dest"]["index"]
        count = self.document_counts.get(source, 0)
        self.document_counts[destination] = count
        return {
            "total": count,
            "created": count,
            "updated": 0,
            "noops": 0,
            "version_conflicts": 0,
            "failures": [],
        }

    async def count(self, *, index: str) -> dict[str, int]:
        if self.count_results:
            return self.count_results.pop(0)
        physical_name = self.indices.resolve(index)
        return {"count": self.document_counts.get(physical_name, 0)}

async def test_redis_migration_lease_renews_until_release() -> None:
    redis = _FakeRedis()
    lease = RedisMigrationLease(
        redis,
        "test_current",
        ttl_ms=100,
        renew_interval_seconds=0.01,
    )

    assert await lease.acquire() is True
    await asyncio.sleep(0.035)
    await lease.ensure_owned()

    renew_calls = [
        call for call in redis.eval_calls if call[0] == RENEW_LOCK_SCRIPT
    ]
    assert len(renew_calls) >= 1
    assert await lease.release() is True
    assert lease.key not in redis.values


async def test_redis_migration_lease_does_not_release_another_owner() -> None:
    redis = _FakeRedis()
    lease = RedisMigrationLease(redis, "test_current")
    assert await lease.acquire() is True
    redis.values[lease.key] = "replacement-owner"

    assert await lease.release() is False
    assert redis.values[lease.key] == "replacement-owner"


async def test_current_schema_does_not_access_redis(
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    fake_migration_redis.set_calls.clear()
    fake_migration_redis.get_calls.clear()
    fake_migration_redis.eval_calls.clear()

    assert await ensure_index(_as_elasticsearch(fake), label) is False
    assert fake_migration_redis.set_calls == []
    assert fake_migration_redis.get_calls == []
    assert fake_migration_redis.eval_calls == []


async def test_lost_redis_lease_does_not_switch_alias(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    old_index = fake.indices.aliases[original.alias]
    alias_update_count = len(fake.indices.update_aliases_calls)
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2, generation=2),
    )
    fake_migration_redis.eval_results[RENEW_LOCK_SCRIPT] = [1, 0]

    with pytest.raises(RuntimeError, match="ownership lost"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == old_index
    assert len(fake.indices.update_aliases_calls) == alias_update_count
    assert fake.indices.write_blocks[old_index] is False


async def test_lock_loser_retries_when_owner_lease_disappears(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2, generation=2),
    )
    monkeypatch.setattr(
        elasticsearch_index,
        "MIGRATION_POLL_INTERVAL_SECONDS",
        0.001,
    )
    owner = RedisMigrationLease(fake_migration_redis, original.alias)
    assert await owner.acquire() is True

    waiter = asyncio.create_task(
        ensure_index(_as_elasticsearch(fake), label)
    )
    for _ in range(100):
        if fake_migration_redis.get_calls:
            break
        await asyncio.sleep(0)
    assert await owner.release() is True

    assert await waiter is True
    current_index = fake.indices.aliases[original.alias]
    assert fake.indices.mappings[current_index]["_meta"][
        INDEX_SCHEMA_META_KEY
    ]["schema_version"] == 2


async def test_lock_loser_observes_schema_only_put_mapping_update(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[original.alias]
    initial_create_count = len(fake.indices.create_calls)
    upgraded = replace(
        original,
        schema_version=2,
        mappings={
            **original.mappings,
            "properties": {"new_field": {"type": "keyword"}},
        },
    )
    monkeypatch.setitem(INDEX_DEFINITIONS, label, upgraded)
    monkeypatch.setattr(
        elasticsearch_index,
        "MIGRATION_POLL_INTERVAL_SECONDS",
        0.001,
    )
    owner = RedisMigrationLease(fake_migration_redis, original.alias)
    assert await owner.acquire() is True

    waiter = asyncio.create_task(
        ensure_index(_as_elasticsearch(fake), label)
    )
    for _ in range(100):
        if fake_migration_redis.get_calls:
            break
        await asyncio.sleep(0)
    await fake.indices.put_mapping(
        index=current_index,
        properties={"new_field": {"type": "keyword"}},
        meta={
            INDEX_SCHEMA_META_KEY: {
                "label": label.name,
                "schema_version": 2,
                "generation": 1,
            }
        },
    )

    assert await waiter is False
    assert fake.indices.aliases[original.alias] == current_index
    assert len(fake.indices.create_calls) == initial_create_count
    assert fake.reindex_calls == []
    assert await owner.release() is True


def _as_elasticsearch(fake: _FakeElasticsearch) -> AsyncElasticsearch:
    return cast(AsyncElasticsearch, fake)


def test_elasticsearch_index_definitions_are_explicit_and_unique() -> None:
    from app.core.memory.storage.provider.elasticsearch.index.definitions import (
        EMBEDDING_DIMS,
        EMBEDDING_FIELDS,
        FULLTEXT_FIELDS,
    )

    expected_versions = {
        MemoryNodeType.ASSISTANT_ORIGINAL: (2, 1),
        MemoryNodeType.ASSISTANT_PRUNED: (2, 1),
        MemoryNodeType.CHUNK: (2, 1),
        MemoryNodeType.COMMUNITY: (2, 1),
        MemoryNodeType.CONVERSATION: (2, 1),
        MemoryNodeType.DIALOGUE: (2, 1),
        MemoryNodeType.EXTRACTED_ENTITY: (2, 1),
        MemoryNodeType.MEMORY_SUMMARY: (2, 1),
        MemoryNodeType.PERCEPTUAL: (2, 1),
        MemoryNodeType.STATEMENT: (2, 1),
        MemoryNodeType.USER_SOURCE: (2, 1),
    }
    production_labels = tuple(MemoryNodeType)
    production_definitions = [
        get_index_definition(label) for label in production_labels
    ]
    registered_definitions = list(INDEX_DEFINITIONS.values())
    aliases = [get_index_name(label) for label in INDEX_DEFINITIONS]

    assert set(INDEX_DEFINITIONS) == {
        *MemoryNodeType,
        TEST_INDEX_LABEL,
    }
    assert INDEX_DEFINITIONS[TEST_INDEX_LABEL] is TEST_INDEX_DEFINITION
    assert set(expected_versions) == set(MemoryNodeType)
    assert aliases == [
        (
            TEST_INDEX_DEFINITION.alias
            if label is TEST_INDEX_LABEL
            else f"{label.name.lower()}_current"
        )
        for label in INDEX_DEFINITIONS
    ]
    registered_count = len(MemoryNodeType) + 1
    assert len(aliases) == len(set(aliases)) == registered_count

    for label, definition in zip(
        production_labels,
        production_definitions,
        strict=True,
    ):
        assert definition.name == label.name.lower()
        assert (
            definition.schema_version,
            definition.generation,
        ) == expected_versions[label]
        assert definition.settings == {
            "number_of_shards": INDEX_SHARD_COUNT
        }

        properties = definition.mappings["properties"]
        search_fields = {
            *FULLTEXT_FIELDS.get(label, ()),
            *(
                (EMBEDDING_FIELDS[label],)
                if label in EMBEDDING_FIELDS
                else ()
            ),
        }
        assert search_fields.issubset(properties)
        assert "id" in properties
        for field in FULLTEXT_FIELDS.get(label, ()):
            assert properties[field] == {
                "type": "text",
                "analyzer": "cjk",
            }
        if label in EMBEDDING_FIELDS:
            assert properties[EMBEDDING_FIELDS[label]] == {
                "type": "dense_vector",
                "dims": EMBEDDING_DIMS,
                "index": True,
                "similarity": "cosine",
            }

    assert len({id(item) for item in registered_definitions}) == registered_count
    assert len(
        {id(definition.settings) for definition in registered_definitions}
    ) == registered_count
    assert len(
        {id(definition.mappings) for definition in registered_definitions}
    ) == registered_count


async def test_ensure_test_index_does_not_touch_production_aliases() -> None:
    fake = _FakeElasticsearch()
    production_aliases = {
        get_index_name(label) for label in MemoryNodeType
    }

    assert await ensure_index(
        _as_elasticsearch(fake),
        TEST_INDEX_LABEL,
    ) is True

    assert set(fake.indices.aliases) == {TEST_INDEX_DEFINITION.alias}
    assert set(fake.indices.aliases).isdisjoint(production_aliases)
    assert len(fake.indices.create_calls) == 1
    storage_meta = fake.indices.create_calls[0]["mappings"]["_meta"][
        INDEX_SCHEMA_META_KEY
    ]
    assert storage_meta["label"] == TEST_INDEX_LABEL.name


async def test_ensure_indices_creates_versioned_indices_and_aliases() -> None:
    fake = _FakeElasticsearch()

    changed = await ensure_indices(_as_elasticsearch(fake))

    assert changed == tuple(
        get_index_name(label) for label in INDEX_DEFINITIONS
    )
    physical_create_calls = fake.indices.create_calls
    assert len(physical_create_calls) == len(INDEX_DEFINITIONS)
    for call in physical_create_calls:
        storage_meta = call["mappings"]["_meta"][INDEX_SCHEMA_META_KEY]
        label = next(
            label
            for label in INDEX_DEFINITIONS
            if label.name == storage_meta["label"]
        )
        definition = get_index_definition(label)
        assert call["index"].startswith(
            f"{definition.name}_g{definition.generation}_"
        )
        assert call["settings"] == definition.settings
        assert call["mappings"]["properties"] == definition.mappings[
            "properties"
        ]
        assert storage_meta["schema_version"] == definition.schema_version
        assert storage_meta["generation"] == definition.generation
        assert fake.indices.aliases[definition.alias] == call["index"]
    assert INDEX_SHARD_COUNT == 5

    assert await ensure_indices(_as_elasticsearch(fake)) == ()


async def test_elastic_client_create_ensures_all_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeElasticsearch()

    async def fake_connect(self: ElasticClient) -> AsyncElasticsearch:
        return _as_elasticsearch(fake)

    monkeypatch.setattr(ElasticClient, "connect", fake_connect)

    client = await ElasticClient.create()

    assert client.client is fake
    assert set(fake.indices.aliases) == {
        get_index_name(label) for label in INDEX_DEFINITIONS
    }
    physical_create_calls = fake.indices.create_calls
    assert len(physical_create_calls) == len(INDEX_DEFINITIONS)
    for call in physical_create_calls:
        storage_meta = call["mappings"]["_meta"][INDEX_SCHEMA_META_KEY]
        label = next(
            label
            for label in INDEX_DEFINITIONS
            if label.name == storage_meta["label"]
        )
        definition = get_index_definition(label)
        assert storage_meta["schema_version"] == definition.schema_version
        assert storage_meta["generation"] == definition.generation


async def test_elastic_client_health_and_close() -> None:
    fake = _FakeElasticsearch()
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    assert await client.health() is True
    assert fake.ping_calls == 1

    await client.close()

    assert fake.closed is True
    assert client.client is None


def test_elasticsearch_document_normalization() -> None:
    value = {
        "created_at": datetime(
            2026,
            1,
            1,
            8,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        "embedding": (1, 2.5),
        "metadata": {"statement": ""},
    }

    assert normalize_elasticsearch_document(value, date_fields=set()) == {
        "created_at": "2026-01-01T00:00:00Z",
        "embedding": [1, 2.5],
        "metadata": {"statement": ""},
    }
    assert normalize_elasticsearch_document(
        {
            "valid_at": "",
            "invalid_at": "   ",
            "statement": "",
        },
        date_fields={"valid_at", "invalid_at"},
    ) == {
        "valid_at": None,
        "invalid_at": None,
        "statement": "",
    }
    invalid_documents = (
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": object()},
        cast(dict[str, Any], {1: "value"}),
    )
    for invalid in invalid_documents:
        with pytest.raises(ValueError):
            normalize_elasticsearch_document(invalid, date_fields=set())


async def test_elastic_client_save_and_update_node() -> None:
    fake = _FakeElasticsearch()
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    save_result = await client.save_node(
        MemoryNodeType.STATEMENT,
        {"id": 123, "status": "pending"},
    )
    update_result = await client.update_node(
        MemoryNodeType.STATEMENT,
        {"status": "completed", "score": 10},
        NodeFilter.eq("tenant_id", "tenant-1"),
    )

    assert fake.index_calls == [
        {
            "index": get_index_name(MemoryNodeType.STATEMENT),
            "id": "123",
            "document": {"id": 123, "status": "pending"},
            "refresh": "wait_for",
        }
    ]
    update_call = fake.update_by_query_calls[0]
    assert update_call["index"] == get_index_name(MemoryNodeType.STATEMENT)
    assert update_call["query"] == {
        "bool": {"filter": [{"term": {"tenant_id": "tenant-1"}}]}
    }
    assert update_call["script"] == {
        "lang": "painless",
        "source": "ctx._source.putAll(params.properties)",
        "params": {"properties": {"status": "completed", "score": 10}},
    }
    assert update_call["conflicts"] == "abort"
    assert update_call["refresh"] is True
    assert save_result.backend == BackendType.ELASTIC
    assert save_result.affected_count == 1
    assert save_result.ids == ["123"]
    assert save_result.data == [{"id": 123, "status": "pending"}]
    assert update_result.backend == BackendType.ELASTIC
    assert update_result.affected_count == 2


async def test_elastic_client_normalizes_only_mapped_blank_date_fields() -> None:
    fake = _FakeElasticsearch()
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    await client.save_node(
        MemoryNodeType.STATEMENT,
        {
            "id": "statement-1",
            "statement": "",
            "valid_at": "",
            "invalid_at": "   ",
        },
    )
    await client.update_node(
        MemoryNodeType.STATEMENT,
        {
            "statement": "",
            "valid_at": "   ",
            "invalid_at": "",
        },
        NodeFilter.eq("id", "statement-1"),
    )

    assert fake.index_calls[0]["document"] == {
        "id": "statement-1",
        "statement": "",
        "valid_at": None,
        "invalid_at": None,
    }
    assert fake.update_by_query_calls[0]["script"]["params"]["properties"] == {
        "statement": "",
        "valid_at": None,
        "invalid_at": None,
    }


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"timed_out": True},
        {"version_conflicts": 1},
        {"_shards": {"failed": 1}},
        {"failures": [{"status": 503}]},
    ],
)
async def test_elastic_client_rejects_incomplete_index_response(
    response: dict[str, Any],
) -> None:
    fake = _FakeElasticsearch()
    fake.index_result = response
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    with pytest.raises(RuntimeError):
        await client.save_node(
            MemoryNodeType.STATEMENT,
            {"id": "node-1"},
        )


async def test_elastic_client_rejects_missing_delete_acknowledgement() -> None:
    fake = _FakeElasticsearch()
    fake.delete_result = {}
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    with pytest.raises(RuntimeError, match="acknowledgement missing"):
        await client.delete_node(
            MemoryNodeType.STATEMENT,
            NodeFilter.eq("id", "node-1"),
        )


async def test_elastic_client_get_node_uses_filter_projection_and_sort() -> None:
    fake = _FakeElasticsearch()
    fake.search_result = {
        "hits": {
            "hits": [
                {"_source": {"id": "node-2", "status": "active"}},
                {"_source": {"id": "node-1", "status": "active"}},
            ]
        }
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.get_node(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.eq("status", "active"),
        projection=NodeProjection.of("id", "status"),
        node_sort=NodeSort.desc("score"),
    )

    assert fake.open_point_in_time_calls == [
        {
            "index": get_index_name(MemoryNodeType.EXTRACTED_ENTITY),
            "keep_alive": PIT_KEEP_ALIVE,
            "allow_partial_search_results": False,
        }
    ]
    assert fake.search_calls == [
        {
            "query": {
                "bool": {"filter": [{"term": {"status": "active"}}]}
            },
            "size": SEARCH_BATCH_SIZE,
            "source_includes": ["id", "status"],
            "sort": [{"score": "desc"}, {"_shard_doc": "asc"}],
            "pit": {"id": "pit-1", "keep_alive": PIT_KEEP_ALIVE},
        }
    ]
    assert fake.close_point_in_time_calls == [{"id": "pit-1"}]
    assert result.items == [
        StorageItem(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            data={"id": "node-2", "status": "active"},
        ),
        StorageItem(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            data={"id": "node-1", "status": "active"},
        ),
    ]


async def test_elastic_client_delete_node_supports_physical_and_draft_modes() -> None:
    fake = _FakeElasticsearch()
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)
    node_filter = NodeFilter.eq("tenant_id", "tenant-1")

    physical_result = await client.delete_node(
        MemoryNodeType.CHUNK,
        node_filter,
    )
    draft_result = await client.delete_node(
        MemoryNodeType.CHUNK,
        node_filter,
        draft=True,
    )

    assert fake.delete_by_query_calls == [
        {
            "index": get_index_name(MemoryNodeType.CHUNK),
            "query": {
                "bool": {"filter": [{"term": {"tenant_id": "tenant-1"}}]}
            },
            "conflicts": "abort",
            "refresh": True,
        }
    ]
    draft_call = fake.update_by_query_calls[0]
    assert draft_call["index"] == get_index_name(MemoryNodeType.CHUNK)
    assert draft_call["query"] == {
        "bool": {
            "filter": [
                {
                    "bool": {
                        "filter": [
                            {"term": {"tenant_id": "tenant-1"}}
                        ]
                    }
                },
                {
                    "bool": {
                        "must_not": {"exists": {"field": "delete_at"}}
                    }
                },
            ]
        }
    }
    assert draft_call["script"]["lang"] == "painless"
    assert draft_call["script"]["source"] == (
        "ctx._source.delete_at = params.delete_at"
    )
    assert draft_call["script"]["params"]["delete_at"].endswith("Z")
    assert draft_call["conflicts"] == "abort"
    assert draft_call["refresh"] is True
    assert physical_result.backend == BackendType.ELASTIC
    assert physical_result.affected_count == 3
    assert draft_result.backend == BackendType.ELASTIC
    assert draft_result.affected_count == 2


def test_elastic_client_requires_connection() -> None:
    client = ElasticClient()

    with pytest.raises(RuntimeError, match="not connected"):
        client._require_client()



async def test_ensure_index_rejects_existing_wrong_shard_count() -> None:
    alias = get_index_name(TEST_INDEX_LABEL)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), TEST_INDEX_LABEL)
    physical_name = fake.indices.aliases[alias]
    fake.indices.settings[physical_name] = {"number_of_shards": 1}

    with pytest.raises(RuntimeError, match="must have 5 primary shards"):
        await ensure_index(_as_elasticsearch(fake), TEST_INDEX_LABEL)


async def test_ensure_index_rejects_missing_configured_mapping() -> None:
    alias = get_index_name(TEST_INDEX_LABEL)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), TEST_INDEX_LABEL)
    physical_name = fake.indices.aliases[alias]
    fake.indices.mappings[physical_name].pop("dynamic_templates")

    with pytest.raises(RuntimeError, match="configured mapping"):
        await ensure_index(_as_elasticsearch(fake), TEST_INDEX_LABEL)



async def test_ensure_index_rebuilds_generation_bump_and_keeps_old_index(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    old_index = fake.indices.aliases[original.alias]
    fake.document_counts[old_index] = 3

    upgraded = replace(original, generation=2)
    monkeypatch.setitem(INDEX_DEFINITIONS, label, upgraded)

    assert await ensure_index(_as_elasticsearch(fake), label) is True

    new_index = fake.indices.aliases[upgraded.alias]
    assert new_index != old_index
    assert new_index.startswith(f"{upgraded.name}_g2_")
    assert old_index in fake.indices.existing
    assert fake.document_counts[new_index] == 3
    assert fake.indices.put_settings_calls[-2:] == [
        {
            "index": old_index,
            "settings": {"index.blocks.write": True},
        },
        {
            "index": old_index,
            "settings": {"index.blocks.write": False},
        },
    ]
    assert fake.indices.refresh_calls[-1] == old_index
    assert fake.indices.write_blocks[old_index] is False
    assert fake_migration_redis.values == {}
    assert fake.reindex_calls[-1] == {
        "source": {"index": old_index},
        "dest": {"index": new_index},
        "conflicts": "abort",
        "refresh": True,
        "wait_for_completion": True,
    }
    assert fake.indices.update_aliases_calls[-1] == [
        {"remove": {"index": old_index, "alias": upgraded.alias}},
        {
            "add": {
                "index": new_index,
                "alias": upgraded.alias,
                "is_write_index": True,
            }
        },
    ]
    storage_metadata = fake.indices.mappings[new_index]["_meta"][
        INDEX_SCHEMA_META_KEY
    ]
    assert storage_metadata["schema_version"] == 1
    assert storage_metadata["generation"] == 2


async def test_schema_version_adds_field_without_reindex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[original.alias]
    create_count = len(fake.indices.create_calls)
    upgraded = replace(
        original,
        schema_version=2,
        mappings={
            **original.mappings,
            "properties": {"new_field": {"type": "keyword"}},
        },
    )
    monkeypatch.setitem(INDEX_DEFINITIONS, label, upgraded)

    assert await ensure_index(_as_elasticsearch(fake), label) is True

    assert fake.indices.aliases[upgraded.alias] == current_index
    assert len(fake.indices.create_calls) == create_count
    assert fake.reindex_calls == []
    assert fake.indices.put_settings_calls == []
    assert fake.indices.put_mapping_calls[-1] == {
        "index": current_index,
        "properties": {"new_field": {"type": "keyword"}},
        "meta": {
            INDEX_SCHEMA_META_KEY: {
                "label": label.name,
                "schema_version": 2,
                "generation": 1,
            }
        },
    }


async def test_dense_vector_change_requires_generation_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    initial = replace(
        original,
        mappings={
            **original.mappings,
            "properties": {
                "embedding": {
                    "type": "dense_vector",
                    "dims": 3,
                    "similarity": "cosine",
                }
            },
        },
    )
    monkeypatch.setitem(INDEX_DEFINITIONS, label, initial)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[initial.alias]
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(
            initial,
            schema_version=2,
            mappings={
                **initial.mappings,
                "properties": {
                    "embedding": {
                        "type": "dense_vector",
                        "dims": 4,
                        "similarity": "cosine",
                    }
                },
            },
        ),
    )

    with pytest.raises(RuntimeError, match="increment generation"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[initial.alias] == current_index
    assert fake.reindex_calls == []
    assert fake.indices.put_mapping_calls == []


async def test_settings_change_requires_generation_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[original.alias]
    upgraded = replace(
        original,
        schema_version=2,
        settings={
            **original.settings,
            "number_of_replicas": 1,
        },
        mappings={
            **original.mappings,
            "properties": {"new_field": {"type": "keyword"}},
        },
    )
    monkeypatch.setitem(INDEX_DEFINITIONS, label, upgraded)

    with pytest.raises(RuntimeError, match="incompatible settings"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == current_index
    assert fake.indices.put_mapping_calls == []
    assert fake.reindex_calls == []


async def test_ensure_index_rejects_alias_with_multiple_targets() -> None:
    label = TEST_INDEX_LABEL
    definition = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[definition.alias]
    fake.indices.get_alias_results[definition.alias] = {
        current_index: {
            "aliases": {
                definition.alias: {"is_write_index": True},
            }
        },
        "stale-index": {
            "aliases": {
                definition.alias: {"is_write_index": False},
            }
        },
    }

    with pytest.raises(RuntimeError, match="exactly one index"):
        await ensure_index(_as_elasticsearch(fake), label)


async def test_ensure_index_rejects_wrong_schema_label() -> None:
    label = TEST_INDEX_LABEL
    definition = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[definition.alias]
    fake.indices.mappings[current_index]["_meta"][INDEX_SCHEMA_META_KEY][
        "label"
    ] = MemoryNodeType.STATEMENT.name
    create_count = len(fake.indices.create_calls)

    with pytest.raises(RuntimeError, match="points to label"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[definition.alias] == current_index
    assert len(fake.indices.create_calls) == create_count


async def test_ensure_index_accepts_legacy_single_version_metadata() -> None:
    label = TEST_INDEX_LABEL
    definition = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[definition.alias]
    fake.indices.mappings[current_index]["_meta"][INDEX_SCHEMA_META_KEY] = {
        "label": label.name,
        "version": 1,
    }
    create_count = len(fake.indices.create_calls)

    assert await ensure_index(_as_elasticsearch(fake), label) is False
    assert fake.indices.aliases[definition.alias] == current_index
    assert len(fake.indices.create_calls) == create_count


async def test_ensure_index_refuses_missing_version_metadata() -> None:
    label = TEST_INDEX_LABEL
    definition = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[definition.alias]
    fake.indices.mappings[current_index]["_meta"].pop(
        INDEX_SCHEMA_META_KEY
    )
    create_count = len(fake.indices.create_calls)

    with pytest.raises(RuntimeError, match="without valid schema identity"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[definition.alias] == current_index
    assert len(fake.indices.create_calls) == create_count


async def test_ensure_index_does_not_switch_on_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    old_index = fake.indices.aliases[original.alias]
    fake.document_counts[old_index] = 3
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2, generation=2),
    )
    fake.count_results = [{"count": 3}, {"count": 2}]
    alias_update_count = len(fake.indices.update_aliases_calls)

    with pytest.raises(RuntimeError, match="count mismatch"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == old_index
    assert fake.indices.write_blocks[old_index] is False
    assert len(fake.indices.update_aliases_calls) == alias_update_count


async def test_ensure_index_refuses_schema_version_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2),
    )
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[original.alias]
    create_count = len(fake.indices.create_calls)
    monkeypatch.setitem(INDEX_DEFINITIONS, label, original)

    with pytest.raises(RuntimeError, match="refusing automatic downgrade"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == current_index
    assert len(fake.indices.create_calls) == create_count


async def test_ensure_index_refuses_generation_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, generation=2),
    )
    await ensure_index(_as_elasticsearch(fake), label)
    current_index = fake.indices.aliases[original.alias]
    create_count = len(fake.indices.create_calls)
    monkeypatch.setitem(INDEX_DEFINITIONS, label, original)

    with pytest.raises(RuntimeError, match="refusing automatic downgrade"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == current_index
    assert len(fake.indices.create_calls) == create_count


async def test_migration_lock_release_failure_preserves_reindex_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2, generation=2),
    )
    fake.reindex_result = {
        "total": 1,
        "created": 0,
        "updated": 0,
        "noops": 0,
        "version_conflicts": 0,
        "failures": [{"cause": "copy failed"}],
    }
    fake_migration_redis.eval_errors[RELEASE_LOCK_SCRIPT] = [
        TimeoutError("lock delete failed")
    ]

    with pytest.raises(RuntimeError, match="reindex failures") as exc_info:
        await ensure_index(_as_elasticsearch(fake), label)

    notes = getattr(exc_info.value, "__notes__", [])
    assert any(
        "failed to release Redis migration lock" in note for note in notes
    )
    lock_key = RedisMigrationLease(
        fake_migration_redis, original.alias
    ).key
    assert lock_key in fake_migration_redis.values


async def test_ensure_index_unblocks_source_when_block_request_times_out(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    old_index = fake.indices.aliases[original.alias]
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2, generation=2),
    )
    fake.indices.put_settings_errors = [TimeoutError("response lost"), None]

    with pytest.raises(TimeoutError, match="response lost"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == old_index
    assert fake.indices.write_blocks[old_index] is False
    assert fake.indices.put_settings_calls[-2:] == [
        {
            "index": old_index,
            "settings": {"index.blocks.write": True},
        },
        {
            "index": old_index,
            "settings": {"index.blocks.write": False},
        },
    ]
    assert fake_migration_redis.values == {}


async def test_ensure_index_does_not_switch_alias_when_reindex_fails(
    monkeypatch: pytest.MonkeyPatch,
    fake_migration_redis: _FakeRedis,
) -> None:
    label = TEST_INDEX_LABEL
    original = get_index_definition(label)
    fake = _FakeElasticsearch()
    await ensure_index(_as_elasticsearch(fake), label)
    old_index = fake.indices.aliases[original.alias]
    alias_update_count = len(fake.indices.update_aliases_calls)
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        label,
        replace(original, schema_version=2, generation=2),
    )
    fake.reindex_result = {
        "total": 1,
        "created": 0,
        "version_conflicts": 0,
        "failures": [{"cause": "boom"}],
    }

    with pytest.raises(RuntimeError, match="reindex failures"):
        await ensure_index(_as_elasticsearch(fake), label)

    assert fake.indices.aliases[original.alias] == old_index
    assert old_index in fake.indices.existing
    assert fake.indices.write_blocks[old_index] is False
    assert fake_migration_redis.values == {}
    assert len(fake.indices.update_aliases_calls) == alias_update_count


async def test_ensure_index_migrates_legacy_index_without_deleting_it() -> None:
    label = TEST_INDEX_LABEL
    definition = get_index_definition(label)
    legacy_index = definition.name
    fake = _FakeElasticsearch(existing_indices={legacy_index})
    fake.document_counts[legacy_index] = 4

    assert await ensure_index(_as_elasticsearch(fake), label) is True

    current_index = fake.indices.aliases[definition.alias]
    assert current_index != legacy_index
    assert legacy_index in fake.indices.existing
    assert fake.document_counts[current_index] == 4
    assert fake.reindex_calls == [
        {
            "source": {"index": legacy_index},
            "dest": {"index": current_index},
            "conflicts": "abort",
            "refresh": True,
            "wait_for_completion": True,
        }
    ]
    assert fake.indices.update_aliases_calls == [
        [
            {
                "add": {
                    "index": current_index,
                    "alias": definition.alias,
                    "is_write_index": True,
                }
            }
        ]
    ]


async def test_elastic_client_get_node_reads_all_pit_pages() -> None:
    fake = _FakeElasticsearch()
    first_page = [
        {
            "_source": {"id": f"node-{index}"},
            "sort": [index],
        }
        for index in range(SEARCH_BATCH_SIZE)
    ]
    fake.search_results = [
        {
            "pit_id": "pit-2",
            "hits": {"hits": first_page},
        },
        {
            "pit_id": "pit-3",
            "hits": {"hits": [{"_source": {"id": "last-node"}}]},

        },
    ]
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.get_node(
        TEST_INDEX_LABEL,
        NodeFilter.eq("category", "pit-test"),
    )

    assert result.total == SEARCH_BATCH_SIZE + 1
    assert len(result.items) == result.total
    assert result.items[-1] == StorageItem(
        label=TEST_INDEX_LABEL,
        data={"id": "last-node"},
    )
    assert len(fake.search_calls) == 2
    assert fake.search_calls[0]["sort"] == [{"_shard_doc": "asc"}]
    assert fake.search_calls[0]["pit"] == {
        "id": "pit-1",
        "keep_alive": PIT_KEEP_ALIVE,
    }
    assert "search_after" not in fake.search_calls[0]
    assert fake.search_calls[1]["pit"] == {
        "id": "pit-2",
        "keep_alive": PIT_KEEP_ALIVE,
    }
    assert fake.search_calls[1]["search_after"] == [SEARCH_BATCH_SIZE - 1]
    assert fake.close_point_in_time_calls == [{"id": "pit-3"}]


async def test_elastic_client_rejects_partial_update_response() -> None:
    fake = _FakeElasticsearch()
    fake.update_result = {
        "updated": 1,
        "version_conflicts": 1,
        "failures": [],
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    with pytest.raises(RuntimeError, match="version conflicts"):
        await client.update_node(
            TEST_INDEX_LABEL,
            {"status": "completed"},
            NodeFilter.eq("id", "node-1"),
        )


async def test_elastic_client_rejects_search_shard_failures() -> None:
    fake = _FakeElasticsearch()
    fake.search_result = {
        "pit_id": "failed-pit-2",
        "_shards": {"failed": 1, "failures": [{"reason": "boom"}]},
        "hits": {"hits": []},
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    with pytest.raises(RuntimeError, match="shard failures"):
        await client.get_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("id", "node-1"),
        )

    assert fake.close_point_in_time_calls == [{"id": "failed-pit-2"}]


@pytest.mark.parametrize(
    ("configured_host", "expected_host"),
    [
        ("localhost", "https://localhost:9200"),
        ("http://localhost:9300", "http://localhost:9300"),
        ("http://[::1]:9400", "http://[::1]:9400"),
    ],
)
def test_build_elasticsearch_client_config_normalizes_hosts(
    monkeypatch: pytest.MonkeyPatch,
    configured_host: str,
    expected_host: str,
) -> None:
    monkeypatch.setattr(settings, "ELASTICSEARCH_HOST", configured_host)
    monkeypatch.setattr(settings, "ELASTICSEARCH_PORT", 9200)

    config = build_elasticsearch_client_config()

    assert config["hosts"] == [expected_host]


async def test_elastic_client_get_node_applies_projection_aliases() -> None:
    from app.core.memory.storage.models import ProjectionField

    fake = _FakeElasticsearch()
    fake.search_result = {
        "hits": {
            "hits": [
                {"_source": {"id": "node-1", "name": "Alice"}},
            ]
        }
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.get_node(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.eq("id", "node-1"),
        projection=NodeProjection.of(
            "id",
            ProjectionField(field="name", alias="display_name"),
        ),
    )

    assert fake.search_calls[0]["source_includes"] == ["id", "name"]
    assert result.items == [
        StorageItem(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            data={"id": "node-1", "display_name": "Alice"},
        ),
    ]



async def test_elastic_client_get_node_evaluates_coalesce_projection() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    fake = _FakeElasticsearch()
    fake.search_result = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "id": "node-1",
                        "nickname": None,
                        "name": "Alice",
                    }
                },
                {"_source": {"id": "node-2"}},
            ]
        }
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)
    projection = NodeProjection.of(
        "id",
        CoalesceProjectionField(
            fields=("nickname", "name"),
            alias="display_name",
            default="Unknown",
        ),
    )

    result = await client.get_node(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.eq("status", "active"),
        projection=projection,
    )

    assert fake.search_calls[0]["source_includes"] == [
        "id",
        "nickname",
        "name",
    ]
    assert result.items == [
        StorageItem(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            data={"id": "node-1", "display_name": "Alice"},
        ),
        StorageItem(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            data={"id": "node-2", "display_name": "Unknown"},
        ),
    ]



async def test_elastic_client_embedding_search_uses_knn_prefilter_and_score() -> None:
    from app.core.memory.storage.models import ProjectionField

    fake = _FakeElasticsearch()
    fake.search_result = {
        "hits": {
            "hits": [
                {
                    "_source": {"id": "node-1", "statement": "hello"},
                    "_score": 0.9,
                }
            ]
        }
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.search_by_embedding(
        MemoryNodeType.STATEMENT,
        NodeFilter.eq("end_user_id", "user-1"),
        [0.1, 0.2, 0.3],
        2,
        projection=NodeProjection.of(
            "id",
            ProjectionField(field="score", alias="similarity"),
        ),
    )

    assert fake.search_calls == [
        {
            "index": get_index_name(MemoryNodeType.STATEMENT),
            "knn": {
                "field": "statement_embedding",
                "query_vector": [0.1, 0.2, 0.3],
                "k": 2,
                "num_candidates": 100,
                "filter": {
                    "bool": {
                        "filter": [{"term": {"end_user_id": "user-1"}}]
                    }
                },
            },
            "size": 2,
            "allow_partial_search_results": False,
            "source_includes": ["id"],
        }
    ]
    assert result.items[0].data["id"] == "node-1"
    assert result.items[0].data["similarity"] == pytest.approx(0.8)


async def test_elastic_client_embedding_search_does_not_add_unrequested_score() -> None:
    fake = _FakeElasticsearch()
    fake.search_result = {
        "hits": {
            "hits": [
                {"_source": {"id": "node-1"}, "_score": 0.95},
            ]
        }
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.search_by_embedding(
        MemoryNodeType.CHUNK,
        NodeFilter.eq("end_user_id", "user-1"),
        [1.0, 0.0],
        1,
    )

    assert result.items == [
        StorageItem(label=MemoryNodeType.CHUNK, data={"id": "node-1"}),
    ]


async def test_elastic_client_fulltext_search_uses_multi_match_filter_and_score() -> None:
    fake = _FakeElasticsearch()
    fake.search_result = {
        "hits": {
            "hits": [
                {"_source": {"id": "entity-1"}, "_score": 3.25},
            ]
        }
    }
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.search_by_fulltext(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.eq("end_user_id", "user-1"),
        "  Alice  ",
        5,
        projection=NodeProjection.of("id", "score"),
    )

    assert fake.search_calls == [
        {
            "index": get_index_name(MemoryNodeType.EXTRACTED_ENTITY),
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": "Alice",
                                "fields": [
                                    "name",
                                    "description",
                                    "aliases",
                                    "description_summary",
                                    "description_timeline",
                                ],
                            }
                        }
                    ],
                    "filter": [
                        {
                            "bool": {
                                "filter": [
                                    {"term": {"end_user_id": "user-1"}}
                                ]
                            }
                        }
                    ],
                }
            },
            "size": 5,
            "allow_partial_search_results": False,
            "source_includes": ["id"],
        }
    ]
    assert result.items == [
        StorageItem(
            label=MemoryNodeType.EXTRACTED_ENTITY,
            data={"id": "entity-1", "score": 3.25},
        ),
    ]


async def test_elastic_client_search_supports_score_only_projection() -> None:
    from app.core.memory.storage.models import ProjectionField

    fake = _FakeElasticsearch()
    fake.search_result = {"hits": {"hits": [{"_score": 2.5}]}}
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.search_by_fulltext(
        MemoryNodeType.STATEMENT,
        NodeFilter.eq("status", "active"),
        "memory",
        1,
        projection=NodeProjection.of(
            ProjectionField(field="score", alias="rank"),
        ),
    )

    assert fake.search_calls[0]["source"] is False
    assert "source_includes" not in fake.search_calls[0]
    assert result.items == [
        StorageItem(label=MemoryNodeType.STATEMENT, data={"rank": 2.5}),
    ]


@pytest.mark.parametrize("limit", [0, -1, True, 10_001])
async def test_elastic_client_search_rejects_invalid_limit(limit: int) -> None:
    client = ElasticClient()
    client.client = _as_elasticsearch(_FakeElasticsearch())

    with pytest.raises(ValueError, match="search limit"):
        await client.search_by_embedding(
            MemoryNodeType.STATEMENT,
            NodeFilter.eq("id", "node-1"),
            [1.0],
            limit,
        )
    with pytest.raises(ValueError, match="search limit"):
        await client.search_by_fulltext(
            MemoryNodeType.STATEMENT,
            NodeFilter.eq("id", "node-1"),
            "memory",
            limit,
        )


@pytest.mark.parametrize(
    "embed",
    [
        [],
        [float("nan")],
        [float("inf")],
        [True],
        ["1.0"],
    ],
)
async def test_elastic_client_embedding_search_rejects_invalid_vector(
    embed: list[Any],
) -> None:
    client = ElasticClient()
    client.client = _as_elasticsearch(_FakeElasticsearch())

    with pytest.raises(ValueError, match="embedding query vector"):
        await client.search_by_embedding(
            MemoryNodeType.STATEMENT,
            NodeFilter.eq("id", "node-1"),
            embed,
            1,
        )


async def test_elastic_client_embedding_search_skips_zero_vector() -> None:
    fake = _FakeElasticsearch()
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.search_by_embedding(
        MemoryNodeType.STATEMENT,
        NodeFilter.eq("id", "node-1"),
        [0.0, 0.0],
        1,
    )

    assert result.backend == BackendType.ELASTIC
    assert result.items == []
    assert result.total == 0
    assert fake.search_calls == []


async def test_elastic_client_fulltext_search_skips_blank_text() -> None:
    fake = _FakeElasticsearch()
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    result = await client.search_by_fulltext(
        MemoryNodeType.STATEMENT,
        NodeFilter.eq("id", "node-1"),
        "   ",
        1,
    )

    assert result.backend == BackendType.ELASTIC
    assert result.items == []
    assert result.total == 0
    assert fake.search_calls == []


async def test_elastic_client_search_rejects_unsupported_label() -> None:
    from app.core.memory.storage.exceptions import UnsupportedQueryError

    client = ElasticClient()
    client.client = _as_elasticsearch(_FakeElasticsearch())

    with pytest.raises(UnsupportedQueryError, match="embedding"):
        await client.search_by_embedding(
            MemoryNodeType.CONVERSATION,
            NodeFilter.eq("id", "node-1"),
            [1.0],
            1,
        )
    with pytest.raises(UnsupportedQueryError, match="fulltext"):
        await client.search_by_fulltext(
            MemoryNodeType.CONVERSATION,
            NodeFilter.eq("id", "node-1"),
            "memory",
            1,
        )


async def test_elastic_client_search_propagates_response_failures() -> None:
    fake = _FakeElasticsearch()
    fake.search_result = {"timed_out": True, "hits": {"hits": []}}
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    with pytest.raises(RuntimeError, match="embedding search timed out"):
        await client.search_by_embedding(
            MemoryNodeType.STATEMENT,
            NodeFilter.eq("id", "node-1"),
            [1.0],
            1,
        )


async def test_elastic_client_search_rejects_missing_requested_score() -> None:
    fake = _FakeElasticsearch()
    fake.search_result = {"hits": {"hits": [{"_source": {"id": "1"}}]}}
    client = ElasticClient()
    client.client = _as_elasticsearch(fake)

    with pytest.raises(RuntimeError, match="invalid _score"):
        await client.search_by_fulltext(
            MemoryNodeType.STATEMENT,
            NodeFilter.eq("id", "node-1"),
            "memory",
            1,
            projection=NodeProjection.of("id", "score"),
        )
