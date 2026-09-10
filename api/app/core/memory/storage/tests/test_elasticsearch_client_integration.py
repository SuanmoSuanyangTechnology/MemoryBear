import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from app.aioRedis import get_thread_safe_redis
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    NodeSort,
    ProjectionField,
)
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.elasticsearch.index import (
    INDEX_SCHEMA_META_KEY,
    ensure_index,
)
from app.core.memory.storage.provider.elasticsearch.index.definitions import (
    EMBEDDING_DIMS,
    EMBEDDING_FIELDS,
    FULLTEXT_FIELDS,
    INDEX_DEFINITIONS,
    INDEX_SHARD_COUNT,
    get_index_definition,
    get_index_name,
)
from app.core.memory.storage.provider.elasticsearch.index.migration_lock import (
    RedisMigrationLease,
)
from app.core.memory.storage.tests.elasticsearch_test_definitions import (
    TEST_INDEX_DEFINITION,
    TEST_INDEX_LABEL,
)

RUN_ELASTICSEARCH_INTEGRATION_TESTS = (
    os.getenv("RUN_ELASTICSEARCH_INTEGRATION_TESTS") == "1"
)
RUN_REDIS_INTEGRATION_TESTS = os.getenv("RUN_REDIS_INTEGRATION_TESTS") == "1"


@pytest.fixture
def registered_test_index_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_INDEX_LABEL not in INDEX_DEFINITIONS
    search_definition = replace(
        TEST_INDEX_DEFINITION,
        schema_version=2,
        mappings={
            **TEST_INDEX_DEFINITION.mappings,
            "properties": {
                **TEST_INDEX_DEFINITION.mappings["properties"],
                "content": {"type": "text", "analyzer": "cjk"},
            },
        },
    )
    monkeypatch.setitem(
        INDEX_DEFINITIONS,
        TEST_INDEX_LABEL,
        search_definition,
    )
    monkeypatch.setitem(FULLTEXT_FIELDS, TEST_INDEX_LABEL, ("content",))
    monkeypatch.setitem(EMBEDDING_FIELDS, TEST_INDEX_LABEL, "embedding")


@pytest.mark.skipif(
    not RUN_REDIS_INTEGRATION_TESTS,
    reason="set RUN_REDIS_INTEGRATION_TESTS=1 to run real Redis tests",
)
async def test_redis_migration_lease_against_real_redis() -> None:
    redis_client = get_thread_safe_redis()
    lease = RedisMigrationLease(
        redis_client,
        f"integration-{uuid4().hex}",
        ttl_ms=300,
        renew_interval_seconds=0.05,
    )

    assert await lease.acquire() is True
    try:
        await asyncio.sleep(0.12)
        await lease.ensure_owned()
    finally:
        assert await lease.release() is True
    assert await redis_client.get(lease.key) is None


@pytest.mark.skipif(
    not RUN_ELASTICSEARCH_INTEGRATION_TESTS,
    reason=(
        "set RUN_ELASTICSEARCH_INTEGRATION_TESTS=1 to run real "
        "Elasticsearch tests"
    ),
)
async def test_elastic_client_crud_and_indices_against_real_elasticsearch(
    registered_test_index_definition: None,
) -> None:
    run_id = str(uuid4())
    suite_marker = "elastic-client-crud-integration"
    category = f"category-{run_id}"
    test_nodes = (
        {
            "id": f"es-integration-{run_id}-1",
            "test_suite": suite_marker,
            "category": category,
            "status": "pending",
            "rank": 10,
        },
        {
            "id": f"es-integration-{run_id}-2",
            "test_suite": suite_marker,
            "category": category,
            "status": "pending",
            "rank": 20,
        },
        {
            "id": f"es-integration-{run_id}-3",
            "test_suite": suite_marker,
            "category": category,
            "status": "pending",
            "rank": 15,
        },
        {
            "id": f"es-integration-{run_id}-4",
            "test_suite": suite_marker,
            "category": category,
            "status": "pending",
            "rank": 5,
        },
    )
    node_ids = [str(node["id"]) for node in test_nodes]
    nodes_filter = NodeFilter(
        conditions=(
            FilterCondition(
                field="id",
                operator=FilterOperator.IN,
                value=node_ids,
            ),
        )
    )
    client = ElasticClient()
    client.client = await client.connect()

    try:
        raw_client = client._require_client()
        await ensure_index(raw_client, TEST_INDEX_LABEL)

        alias = get_index_name(TEST_INDEX_LABEL)
        definition = get_index_definition(TEST_INDEX_LABEL)
        alias_response = await raw_client.indices.get_alias(name=alias)
        assert len(alias_response) == 1
        physical_name = next(iter(alias_response))
        assert physical_name.startswith(
            f"{definition.name}_g{definition.generation}_"
        )

        index_settings = await raw_client.indices.get_settings(
            index=physical_name
        )
        shard_count = index_settings[physical_name]["settings"]["index"][
            "number_of_shards"
        ]
        assert int(shard_count) == INDEX_SHARD_COUNT

        mappings = await raw_client.indices.get_mapping(index=physical_name)
        storage_meta = mappings[physical_name]["mappings"]["_meta"][
            INDEX_SCHEMA_META_KEY
        ]
        assert storage_meta == {
            "label": TEST_INDEX_LABEL.name,
            "schema_version": definition.schema_version,
            "generation": definition.generation,
        }

        await client.delete_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("test_suite", suite_marker),
        )
        for node in test_nodes:
            await client.save_node(TEST_INDEX_LABEL, node)

        sorted_nodes = await client.get_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("category", category),
            projection=NodeProjection.of("id", "status"),
            node_sort=NodeSort.desc("rank"),
        )
        assert sorted_nodes.items == [
            {"id": node_ids[1], "status": "pending"},
            {"id": node_ids[2], "status": "pending"},
            {"id": node_ids[0], "status": "pending"},
            {"id": node_ids[3], "status": "pending"},
        ]

        update_result = await client.update_node(
            TEST_INDEX_LABEL,
            {"status": "completed"},
            NodeFilter.eq("id", node_ids[0]),
        )
        assert update_result.affected_count == 1

        draft_result = await client.delete_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("id", node_ids[0]),
            draft=True,
        )
        repeated_draft_result = await client.delete_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("id", node_ids[0]),
            draft=True,
        )
        drafted = await client.get_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("id", node_ids[0]),
            projection=NodeProjection.of("id", "status", "delete_at"),
        )

        assert draft_result.affected_count == 1
        assert repeated_draft_result.affected_count == 0
        assert drafted.items[0]["status"] == "completed"
        assert drafted.items[0]["delete_at"]

        delete_result = await client.delete_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("id", node_ids[1]),
        )
        assert delete_result.affected_count == 1
        assert (await client.get_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("id", node_ids[1]),
        )).items == []

        retained_nodes = await client.get_node(
            TEST_INDEX_LABEL,
            nodes_filter,
            projection=NodeProjection.of("id"),
        )
        assert {node["id"] for node in retained_nodes.items} == {
            node_ids[0],
            node_ids[2],
            node_ids[3],
        }
    finally:
        await client.close()


@pytest.mark.skipif(
    not RUN_ELASTICSEARCH_INTEGRATION_TESTS,
    reason=(
        "set RUN_ELASTICSEARCH_INTEGRATION_TESTS=1 to run real "
        "Elasticsearch tests"
    ),
)
async def test_elastic_client_search_against_real_elasticsearch(
    registered_test_index_definition: None,
) -> None:
    run_id = uuid4().hex
    suite_marker = "elastic-client-search-integration"
    category = f"search-category-{run_id}"

    first_vector = [0.0] * EMBEDDING_DIMS
    first_vector[0] = 1.0
    second_vector = [0.0] * EMBEDDING_DIMS
    second_vector[1] = 1.0
    nodes = (
        {
            "id": f"search-{run_id}-1",
            "test_suite": suite_marker,
            "category": category,
            "content": f"redbear unique memory {run_id}",
            "embedding": first_vector,
        },
        {
            "id": f"search-{run_id}-2",
            "test_suite": suite_marker,
            "category": category,
            "content": "unrelated content",
            "embedding": second_vector,
        },
        {
            "id": f"search-{run_id}-filtered",
            "test_suite": suite_marker,
            "category": "different-category",
            "content": f"redbear unique memory {run_id}",
            "embedding": first_vector,
        },
    )

    client = ElasticClient()
    client.client = await client.connect()
    try:
        raw_client = client._require_client()
        await ensure_index(raw_client, TEST_INDEX_LABEL)
        await client.delete_node(
            TEST_INDEX_LABEL,
            NodeFilter.eq("test_suite", suite_marker),
        )
        for node in nodes:
            await client.save_node(TEST_INDEX_LABEL, node)

        embedding_results = await client.search_by_embedding(
            TEST_INDEX_LABEL,
            NodeFilter.eq("category", category),
            first_vector,
            2,
            projection=NodeProjection.of("id", "score"),
        )
        assert embedding_results.items[0]["id"] == nodes[0]["id"]
        assert embedding_results.items[0]["score"] == pytest.approx(1.0)
        assert {item["id"] for item in embedding_results.items}.isdisjoint(
            {nodes[2]["id"]}
        )

        fulltext_results = await client.search_by_fulltext(
            TEST_INDEX_LABEL,
            NodeFilter.eq("category", category),
            f"unique memory {run_id}",
            5,
            projection=NodeProjection.of(
                "id",
                ProjectionField(field="score", alias="rank"),
            ),
        )
        assert [item["id"] for item in fulltext_results.items] == [
            nodes[0]["id"]
        ]
        assert fulltext_results.items[0]["rank"] > 0
    finally:
        if client.client is not None:
            await client.delete_node(
                TEST_INDEX_LABEL,
                NodeFilter.eq("test_suite", suite_marker),
            )
        await client.close()
