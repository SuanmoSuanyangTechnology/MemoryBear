"""节点主写、关系主写与 outbox 入队的路由契约。"""

from unittest.mock import AsyncMock, Mock

import pytest

from app.core.memory.storage.enums import (
    BackendType,
    MemoryNodeType,
    MemoryRelationshipType,
    StorageBackendType,
)
from app.core.memory.storage.models import (
    NodeFilter,
    RelationshipFilter,
    StorageWriteResult,
)
from app.core.memory.storage.outbox.types import OutboxOperation
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.router.write_router import WriteRouter
from app.core.memory.storage.service import MemoryStorageService

LABEL = MemoryNodeType.STATEMENT
WRITE_DIMENSIONS = (
    StorageBackendType.GRAPH_MAIN_WRITE,
    StorageBackendType.TEXT_MAIN_WRITE,
    StorageBackendType.VECTOR_MAIN_WRITE,
)


def write_result(*ids: str) -> StorageWriteResult:
    return StorageWriteResult(
        backend=BackendType.NEO4J,
        affected_count=len(ids),
        ids=list(ids),
    )


def neo4j_client(result: StorageWriteResult | None = None):
    result = result or write_result()
    return Mock(
        save_node=AsyncMock(return_value=result),
        update_node=AsyncMock(return_value=result),
        delete_node=AsyncMock(return_value=result),
        save_relationship=AsyncMock(return_value=result),
        update_relationship=AsyncMock(return_value=result),
        delete_relationship=AsyncMock(return_value=result),
    )


def factory(client):
    instance = BackendFactory()
    instance._clients = {
        BackendType.NEO4J: client,
        BackendType.ELASTIC: Mock(name="elastic-must-not-be-written"),
    }
    return instance


def router(client, repository):
    return WriteRouter(factory(client), outbox_repository=repository)


def repository():
    return Mock(
        enqueue_many=AsyncMock(
            side_effect=lambda events: [event.id for event in events]
        )
    )


def enqueued(repo):
    return [
        (item.label, item.node_id, item.operation)
        for call in repo.enqueue_many.await_args_list
        for item in call.args[0]
    ]


@pytest.mark.parametrize("dimension", WRITE_DIMENSIONS)
def test_all_write_dimensions_route_to_neo4j(dimension):
    client = neo4j_client()
    assert factory(client).get_write_client(LABEL, dimension) is client


@pytest.mark.parametrize(
    "dimension",
    [StorageBackendType.GRAPH_MAIN_READ, StorageBackendType.TEXT_NODE],
)
def test_non_write_dimensions_are_rejected(dimension):
    with pytest.raises(ValueError):
        factory(neo4j_client()).get_write_client(LABEL, dimension)


async def test_save_node_returns_provider_result_and_enqueues_upsert():
    expected = write_result("node-1")
    client, repo = neo4j_client(expected), repository()
    data = {"id": "node-1", "text": "hello"}

    result = await router(client, repo).save_node(LABEL, data)

    assert result is expected
    client.save_node.assert_awaited_once_with(LABEL, data)
    assert enqueued(repo) == [(LABEL, "node-1", OutboxOperation.UPSERT)]


async def test_update_node_uses_ids_returned_by_same_write():
    expected = write_result("node-1", "node-2")
    client, repo = neo4j_client(expected), repository()
    node_filter = NodeFilter.eq("end_user_id", "user-1")
    data = {"text": "updated"}

    result = await router(client, repo).update_node(LABEL, data, node_filter)

    assert result is expected
    client.update_node.assert_awaited_once_with(LABEL, data, node_filter)
    assert enqueued(repo) == [
        (LABEL, "node-1", OutboxOperation.UPSERT),
        (LABEL, "node-2", OutboxOperation.UPSERT),
    ]


@pytest.mark.parametrize(
    ("draft", "operation"),
    [
        (False, OutboxOperation.DELETE),
        (True, OutboxOperation.DRAFT_DELETE),
    ],
)
async def test_delete_node_enqueues_operation_for_provider_ids(draft, operation):
    expected = write_result("node-1", "node-2")
    client, repo = neo4j_client(expected), repository()
    node_filter = NodeFilter.eq("end_user_id", "user-1")

    result = await router(client, repo).delete_node(
        LABEL,
        node_filter,
        draft=draft,
    )

    assert result is expected
    client.delete_node.assert_awaited_once_with(LABEL, node_filter, draft)
    assert enqueued(repo) == [
        (LABEL, "node-1", operation),
        (LABEL, "node-2", operation),
    ]


@pytest.mark.parametrize("method", ["save_node", "update_node", "delete_node"])
async def test_empty_write_result_creates_no_event(method):
    expected = write_result()
    client, repo = neo4j_client(expected), repository()
    write_router = router(client, repo)
    node_filter = NodeFilter.eq("id", "missing")
    arguments = {
        "save_node": (LABEL, {"id": "missing"}),
        "update_node": (LABEL, {"text": "x"}, node_filter),
        "delete_node": (LABEL, node_filter),
    }

    assert await getattr(write_router, method)(*arguments[method]) is expected
    repo.enqueue_many.assert_not_awaited()


@pytest.mark.parametrize(
    "result",
    [
        StorageWriteResult(affected_count=1),
        StorageWriteResult(affected_count=2, ids=["node-1"]),
        StorageWriteResult(affected_count=2, ids=["node-1", "node-1"]),
    ],
)
async def test_incomplete_or_duplicate_provider_ids_never_enqueue(result):
    client, repo = neo4j_client(result), repository()

    with pytest.raises(
        ValueError,
        match="one unique id per affected node",
    ):
        await router(client, repo).save_node(LABEL, {"id": "node-1"})

    client.save_node.assert_awaited_once()
    repo.enqueue_many.assert_not_awaited()


async def test_save_relationship_uses_neo4j_and_creates_no_event():
    expected = write_result("edge-1")
    client, repo = neo4j_client(expected), repository()
    data = {"id": "edge-1", "statement": "s"}

    result = await router(client, repo).save_relationship(
        MemoryRelationshipType.RELATES_TO,
        "node-1",
        "node-2",
        data,
    )

    assert result is expected
    client.save_relationship.assert_awaited_once_with(
        MemoryRelationshipType.RELATES_TO,
        "node-1",
        "node-2",
        data,
    )
    repo.enqueue_many.assert_not_awaited()


async def test_update_relationship_uses_neo4j_and_creates_no_event():
    expected = write_result("edge-1")
    client, repo = neo4j_client(expected), repository()
    rel_filter = RelationshipFilter(
        relationship=NodeFilter.eq("id", "edge-1")
    )
    data = {"weight": 0.9}

    result = await router(client, repo).update_relationship(
        MemoryRelationshipType.RELATES_TO,
        data,
        rel_filter,
    )

    assert result is expected
    client.update_relationship.assert_awaited_once_with(
        MemoryRelationshipType.RELATES_TO,
        data,
        rel_filter,
    )
    repo.enqueue_many.assert_not_awaited()


async def test_delete_relationship_uses_neo4j_and_creates_no_event():
    expected = write_result("edge-1")
    client, repo = neo4j_client(expected), repository()
    rel_filter = RelationshipFilter(
        relationship=NodeFilter.eq("id", "edge-1")
    )

    result = await router(client, repo).delete_relationship(
        MemoryRelationshipType.RELATES_TO,
        rel_filter,
    )

    assert result is expected
    client.delete_relationship.assert_awaited_once_with(
        MemoryRelationshipType.RELATES_TO,
        rel_filter,
    )
    repo.enqueue_many.assert_not_awaited()


async def test_enqueue_failure_surfaces_after_primary_commit():
    from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError

    client = neo4j_client(write_result("node-1"))
    repo = Mock(enqueue_many=AsyncMock(side_effect=RuntimeError("secret SQL")))

    with pytest.raises(OutboxEnqueueError) as caught:
        await router(client, repo).save_node(LABEL, {"id": "node-1"})

    client.save_node.assert_awaited_once()
    assert caught.value.primary_committed is True
    assert "secret" not in str(caught.value)


RELATIONSHIP_FILTER = RelationshipFilter(
    relationship=NodeFilter.eq("id", "edge-1")
)

WRITE_DELEGATIONS = [
    ("save_node", (LABEL, {"id": "node-1"})),
    ("update_node", (LABEL, {"text": "x"}, NodeFilter.eq("id", "node-1"))),
    ("delete_node", (LABEL, NodeFilter.eq("id", "node-1"), True)),
    (
        "save_relationship",
        (
            MemoryRelationshipType.RELATES_TO,
            "node-1",
            "node-2",
            {"id": "edge-1"},
        ),
    ),
    (
        "update_relationship",
        (
            MemoryRelationshipType.RELATES_TO,
            {"weight": 0.9},
            RELATIONSHIP_FILTER,
        ),
    ),
    (
        "delete_relationship",
        (
            MemoryRelationshipType.RELATES_TO,
            RELATIONSHIP_FILTER,
        ),
    ),
]


@pytest.mark.parametrize("method,args", WRITE_DELEGATIONS)
async def test_service_write_methods_delegate_to_write_router(method, args):
    service = MemoryStorageService(factory(neo4j_client()))
    expected = write_result("node-1")
    delegate = AsyncMock(return_value=expected)
    setattr(service._write_router, method, delegate)

    assert await getattr(service, method)(*args) is expected
    delegate.assert_awaited_once_with(*args)
