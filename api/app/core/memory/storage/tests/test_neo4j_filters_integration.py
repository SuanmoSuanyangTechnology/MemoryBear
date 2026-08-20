import os
from uuid import uuid4

import pytest

from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    NodeSort,
    SortDirection,
    SortField,
)
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
from app.core.memory.storage.tests.enums import TestMemoryNodeType


RUN_NEO4J_INTEGRATION_TESTS = os.getenv("RUN_NEO4J_INTEGRATION_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_NEO4J_INTEGRATION_TESTS,
    reason="set RUN_NEO4J_INTEGRATION_TESTS=1 to run real Neo4j tests",
)

TEST_NODE_PROPERTIES = {
    "id": 1,
    "name": "test",
    "change": False,
    "score": 100,
    "status": "pending",
    "category": "memory",
}


def _build_filter_cases(node_id: str) -> tuple[tuple[str, NodeFilter], ...]:
    id_condition = FilterCondition(field="id", value=node_id)

    def for_test_node(*conditions: FilterCondition | NodeFilter) -> NodeFilter:
        return NodeFilter.all_of(id_condition, *conditions)

    return (
        ("eq", NodeFilter.eq("id", node_id)),
        (
            "ne",
            for_test_node(
                FilterCondition(
                    field="name",
                    operator=FilterOperator.NE,
                    value="other",
                )
            ),
        ),
        (
            "gt",
            for_test_node(
                FilterCondition(
                    field="score",
                    operator=FilterOperator.GT,
                    value=99,
                )
            ),
        ),
        (
            "gte",
            for_test_node(
                FilterCondition(
                    field="score",
                    operator=FilterOperator.GTE,
                    value=100,
                )
            ),
        ),
        (
            "lt",
            for_test_node(
                FilterCondition(
                    field="score",
                    operator=FilterOperator.LT,
                    value=101,
                )
            ),
        ),
        (
            "lte",
            for_test_node(
                FilterCondition(
                    field="score",
                    operator=FilterOperator.LTE,
                    value=100,
                )
            ),
        ),
        (
            "in",
            for_test_node(
                FilterCondition(
                    field="status",
                    operator=FilterOperator.IN,
                    value=["pending", "running"],
                )
            ),
        ),
        (
            "not_in",
            for_test_node(
                FilterCondition(
                    field="category",
                    operator=FilterOperator.NOT_IN,
                    value=["ignored", "deleted"],
                )
            ),
        ),
        (
            "exists_true",
            for_test_node(
                FilterCondition(
                    field="name",
                    operator=FilterOperator.EXISTS,
                    value=True,
                )
            ),
        ),
        (
            "exists_false",
            for_test_node(
                FilterCondition(
                    field="deleted_at",
                    operator=FilterOperator.EXISTS,
                    value=False,
                )
            ),
        ),
        (
            "eq_none",
            for_test_node(
                FilterCondition(
                    field="deleted_at",
                    operator=FilterOperator.EQ,
                    value=None,
                )
            ),
        ),
        (
            "ne_none",
            for_test_node(
                FilterCondition(
                    field="name",
                    operator=FilterOperator.NE,
                    value=None,
                )
            ),
        ),
        (
            "and",
            for_test_node(
                FilterCondition(field="name", value="test"),
                FilterCondition(field="status", value="pending"),
            ),
        ),
        (
            "or",
            for_test_node(
                NodeFilter.any_of(
                    FilterCondition(field="status", value="missing"),
                    FilterCondition(field="status", value="pending"),
                )
            ),
        ),
        (
            "nested",
            for_test_node(
                NodeFilter.any_of(
                    FilterCondition(field="status", value="missing"),
                    NodeFilter.all_of(
                        FilterCondition(
                            field="score",
                            operator=FilterOperator.GTE,
                            value=100,
                        ),
                        FilterCondition(field="category", value="memory"),
                    ),
                )
            ),
        ),
    )


async def test_all_filters_against_real_neo4j() -> None:
    node_id = f"filter-test-{uuid4()}"
    node = {**TEST_NODE_PROPERTIES, "id": node_id}
    client = await Neo4jClient.create()
    try:
        await client.save_node(TestMemoryNodeType.TEST, node)

        filter_cases = _build_filter_cases(node_id)
        assert len(filter_cases) == 15

        for case_name, node_filter in filter_cases:
            result = await client.update_node(
                TestMemoryNodeType.TEST,
                {"change": True, f"test_{case_name}": True},
                node_filter,
            )
            assert result, f"Filter case did not match the test node: {case_name}"

        projected_result = await client.get_node(
            TestMemoryNodeType.TEST,
            NodeFilter.eq("id", node_id),
            projection=NodeProjection.of("id", "name"),
        )
        assert projected_result == [{"id": node_id, "name": "test"}]
    finally:
        try:
            await _cleanup_test_node(client, node_id)
        finally:
            await client.close()



async def test_get_node_sorts_by_unprojected_property_in_real_neo4j() -> None:
    run_id = uuid4()
    test_nodes = (
        {"id": f"node-sort-test-{run_id}-1", "category": "sort-test", "score": 20},
        {"id": f"node-sort-test-{run_id}-2", "category": "sort-test", "score": 10},
        {"id": f"node-sort-test-{run_id}-3", "category": "sort-test", "score": 20},
    )
    test_node_ids = [node["id"] for node in test_nodes]
    client = await Neo4jClient.create()

    try:
        for node in test_nodes:
            await client.save_node(TestMemoryNodeType.TEST, node)

        result = await client.get_node(
            TestMemoryNodeType.TEST,
            NodeFilter(
                conditions=(
                    FilterCondition(
                        field="id",
                        operator=FilterOperator.IN,
                        value=test_node_ids,
                    ),
                )
            ),
            projection=NodeProjection.of("id"),
            node_sort=NodeSort(
                fields=(
                    SortField(field="score", direction=SortDirection.DESC),
                    SortField(field="id", direction=SortDirection.ASC),
                )
            ),
        )

        assert result == [
            {"id": test_node_ids[0]},
            {"id": test_node_ids[2]},
            {"id": test_node_ids[1]},
        ]
    finally:
        try:
            if client.client is not None:
                async with client.client.session() as session:
                    cleanup = await session.run(
                        "MATCH (n:Test) WHERE n.id IN $ids DETACH DELETE n",
                        ids=test_node_ids,
                    )
                    await cleanup.consume()
        finally:
            await client.close()


async def _cleanup_test_node(client: Neo4jClient, node_id: str) -> None:
    if client.client is None:
        return

    async with client.client.session() as session:
        cleanup = await session.run(
            "MATCH (n:Test {id: $id}) DETACH DELETE n",
            id=node_id,
        )
        await cleanup.consume()


async def test_delete_node_physically_deletes_from_real_neo4j() -> None:
    node_id = f"delete-physical-{uuid4()}"
    node_filter = NodeFilter.eq("id", node_id)
    client = await Neo4jClient.create()

    try:
        await client.save_node(
            TestMemoryNodeType.TEST,
            {"id": node_id, "name": "physical-delete-test"},
        )

        result = await client.delete_node(
            TestMemoryNodeType.TEST,
            node_filter=node_filter,
        )

        assert result == [{"deleted": 1}]
        assert await client.get_node(TestMemoryNodeType.TEST, node_filter) == []
    finally:
        try:
            await _cleanup_test_node(client, node_id)
        finally:
            await client.close()


async def test_delete_node_draft_soft_deletes_in_real_neo4j() -> None:
    node_id = f"delete-draft-{uuid4()}"
    node_filter = NodeFilter.eq("id", node_id)
    client = await Neo4jClient.create()

    try:
        await client.save_node(
            TestMemoryNodeType.TEST,
            {"id": node_id, "name": "draft-delete-test"},
        )

        first_result = await client.delete_node(
            TestMemoryNodeType.TEST,
            node_filter=node_filter,
            draft=True,
        )
        projected = await client.get_node(
            TestMemoryNodeType.TEST,
            node_filter,
            projection=NodeProjection.of("id", "delete_at"),
        )
        second_result = await client.delete_node(
            TestMemoryNodeType.TEST,
            node_filter=node_filter,
            draft=True,
        )

        assert first_result == [{"deleted": 1}]
        assert len(projected) == 1
        assert projected[0]["id"] == node_id
        assert projected[0]["delete_at"] is not None
        assert second_result == [{"deleted": 0}]

        physical_result = await client.delete_node(
            TestMemoryNodeType.TEST,
            node_filter=node_filter,
        )
        assert physical_result == [{"deleted": 1}]
        assert await client.get_node(TestMemoryNodeType.TEST, node_filter) == []
    finally:
        try:
            await _cleanup_test_node(client, node_id)
        finally:
            await client.close()
