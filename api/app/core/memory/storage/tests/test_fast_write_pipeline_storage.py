"""Fast write pipeline storage integration: Dialogue via save_memory_graph + outbox."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.memory.models.graph_models import DialogueNode
from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.models import GraphWriteResult
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.service import MemoryStorageService


def _dialogue_node() -> DialogueNode:
    return DialogueNode(
        id="dialog-test-1",
        name="dialog-test-1",
        end_user_id="user-1",
        run_id="run-1",
        created_at=datetime(2026, 9, 1),
        ref_id="ref-1",
        content="hello",
        dialog_embedding=None,
        config_id="config-1",
        write_mode="fast",
        emotion="joy",
        emotion_score=0.8,
    )


class _FastPipelineStub:
    """Minimal stub matching FastWritePipeline's attribute surface for _persist."""

    NEO4J_MERGE_MAX_RETRY = 3
    end_user_id = "user-1"

    def __init__(self, storage_service):
        self._storage_service = storage_service

    async def _init_storage_service(self) -> None:
        pass

    _persist = None  # bound below


from app.core.memory.pipelines.fast_write_pipeline import FastWritePipeline
_FastPipelineStub._persist = FastWritePipeline._persist


def _pipeline(storage_service) -> _FastPipelineStub:
    return _FastPipelineStub(storage_service)


async def test_fast_write_persists_dialogue_via_save_memory_graph() -> None:
    storage = Mock()
    storage.save_memory_graph = AsyncMock(return_value=GraphWriteResult(
        node_ids={MemoryNodeType.DIALOGUE: ["dialog-test-1"]},
    ))

    dialog_id = await _pipeline(storage)._persist(_dialogue_node())

    assert dialog_id == "dialog-test-1"
    storage.save_memory_graph.assert_awaited_once()
    command = storage.save_memory_graph.await_args.args[0]
    assert command.dialogue_nodes == [_dialogue_node()]
    assert command.dialogue_nodes[0].write_mode == "fast"


async def test_fast_write_delegates_to_storage_service_for_outbox() -> None:
    """_persist calls save_memory_graph which is the WriteRouter entry point.

    WriteRouter internally calls enqueue_events after Neo4j commit; that contract
    is covered by test_write_router.py. Here we verify _persist passes the
    dialogue node with write_mode='fast' to save_memory_graph, which is the
    single entry point for both Neo4j write and outbox enqueue.
    """
    storage = Mock()
    storage.save_memory_graph = AsyncMock(return_value=GraphWriteResult(
        node_ids={MemoryNodeType.DIALOGUE: ["dialog-test-1"]},
    ))

    await _pipeline(storage)._persist(_dialogue_node())

    storage.save_memory_graph.assert_awaited_once()
    command = storage.save_memory_graph.await_args.args[0]
    assert len(command.dialogue_nodes) == 1
    assert command.dialogue_nodes[0].id == "dialog-test-1"
    assert command.dialogue_nodes[0].write_mode == "fast"


async def test_fast_write_retries_on_deadlock() -> None:
    storage = Mock()
    storage.save_memory_graph = AsyncMock(side_effect=[
        RuntimeError("Neo4j deadlock detected"),
        GraphWriteResult(node_ids={MemoryNodeType.DIALOGUE: ["dialog-test-1"]}),
    ])

    import app.core.memory.pipelines.fast_write_pipeline as fwp
    orig_sleep = fwp.asyncio.sleep
    fwp.asyncio.sleep = AsyncMock()
    try:
        dialog_id = await _pipeline(storage)._persist(_dialogue_node())
    finally:
        fwp.asyncio.sleep = orig_sleep

    assert dialog_id == "dialog-test-1"
    assert storage.save_memory_graph.await_count == 2


async def test_fast_write_raises_on_non_deadlock_error() -> None:
    storage = Mock()
    storage.save_memory_graph = AsyncMock(
        side_effect=RuntimeError("connection refused")
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        await _pipeline(storage)._persist(_dialogue_node())

    assert storage.save_memory_graph.await_count == 1


async def test_fast_write_surfaces_outbox_enqueue_failure() -> None:
    """Outbox enqueue failure (raised by save_memory_graph / WriteRouter)
    propagates from _persist without being swallowed as deadlock."""
    storage = Mock()
    storage.save_memory_graph = AsyncMock(
        side_effect=OutboxEnqueueError([], "ConnectionError")
    )

    with pytest.raises(OutboxEnqueueError):
        await _pipeline(storage)._persist(_dialogue_node())

    assert storage.save_memory_graph.await_count == 1


async def test_graph_write_only_factory_creates_only_neo4j(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    neo4j_client = Mock(close=AsyncMock())
    create_neo4j = AsyncMock(return_value=neo4j_client)
    create_elastic = AsyncMock(
        side_effect=AssertionError("write-only factory must not create Elasticsearch")
    )

    class Neo4jClientType:
        create = create_neo4j

    class ElasticClientType:
        create = create_elastic

    monkeypatch.setitem(
        BackendFactory.BACKENDS,
        BackendType.NEO4J,
        Neo4jClientType,
    )
    monkeypatch.setitem(
        BackendFactory.BACKENDS,
        BackendType.ELASTIC,
        ElasticClientType,
    )

    factory = await BackendFactory.create_graph_write_only()

    assert factory.get_graph_write_client() is neo4j_client
    create_neo4j.assert_awaited_once_with()
    create_elastic.assert_not_awaited()
    with pytest.raises(RuntimeError, match="not initialized"):
        factory.get_client(BackendType.ELASTIC)

    await factory.close()
    neo4j_client.close.assert_awaited_once_with()


async def test_fast_write_owns_and_closes_graph_write_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Mock(close=AsyncMock())
    create = AsyncMock(return_value=service)
    monkeypatch.setattr(
        MemoryStorageService,
        "create_graph_write_only",
        create,
    )
    pipeline = FastWritePipeline(Mock(), "user-1")

    await pipeline._init_storage_service()
    await pipeline._init_storage_service()

    create.assert_awaited_once_with()
    assert pipeline._storage_service is service

    await pipeline._cleanup()

    service.close.assert_awaited_once_with()
    assert pipeline._storage_service is None


async def test_fast_write_cleanup_clears_service_when_close_fails() -> None:
    service = Mock(close=AsyncMock(side_effect=RuntimeError("close failed")))
    pipeline = FastWritePipeline(Mock(), "user-1")
    pipeline._storage_service = service

    await pipeline._cleanup()

    service.close.assert_awaited_once_with()
    assert pipeline._storage_service is None
