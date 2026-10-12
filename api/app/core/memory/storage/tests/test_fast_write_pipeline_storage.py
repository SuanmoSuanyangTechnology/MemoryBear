"""Fast write pipeline storage integration: Dialogue via save_memory_graph + outbox."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.memory.enums import StorageType
from app.core.memory.models.graph_models import DialogueNode
from app.core.memory.pipelines import dispatcher
from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.models import GraphWriteResult
from app.core.memory.storage.outbox.exceptions import OutboxEnqueueError
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.service import MemoryStorageService


async def test_rag_storage_does_not_have_fast_write_permission() -> None:
    assert not await dispatcher.check_fast_write_permission(
        role="user",
        should_memorize=True,
        storage_type="rag",
    )


async def test_neo4j_storage_keeps_fast_write_permission() -> None:
    assert await dispatcher.check_fast_write_permission(
        role="user",
        should_memorize=True,
        storage_type="neo4j",
    )


async def test_rag_enum_does_not_have_fast_write_permission() -> None:
    assert not await dispatcher.check_fast_write_permission(
        role="user",
        should_memorize=True,
        storage_type=StorageType.RAG,
    )


async def test_rag_storage_does_not_push_fast_write_task(monkeypatch) -> None:
    push_fast_write_task = AsyncMock()
    monkeypatch.setattr(dispatcher, "push_fast_write_task", push_fast_write_task)

    await dispatcher.safe_push_fast_write(
        role="user",
        should_memorize=True,
        storage_type="RAG",
        end_user_id="end-user-1",
        target_message={"role": "user", "content": "hello"},
        config_id="config-1",
        workspace_id="workspace-1",
    )

    push_fast_write_task.assert_not_awaited()


async def test_rag_agent_write_bypasses_normal_and_fast_dispatch(monkeypatch) -> None:
    rag_write = AsyncMock()
    normal_dispatch = AsyncMock()
    fast_dispatch = AsyncMock()
    monkeypatch.setattr(dispatcher, "check_memory_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(dispatcher, "write_messages_to_rag", rag_write)
    monkeypatch.setattr(dispatcher, "check_sliding_window_and_dispatch", normal_dispatch)
    monkeypatch.setattr(dispatcher, "safe_push_fast_write", fast_dispatch)
    messages = [
        SimpleNamespace(role="user", content="remember me", should_memorize=True),
        SimpleNamespace(role="assistant", content="okay", should_memorize=True),
    ]

    result = await dispatcher.ingest_agent_messages(
        conversation_id="conversation-1",
        messages=messages,
        app_id="app-1",
        end_user_id="end-user-1",
        storage_type="rag",
        user_rag_memory_id="knowledge-1",
    )

    assert result is True
    rag_write.assert_awaited_once_with(
        messages=messages,
        end_user_id="end-user-1",
        user_rag_memory_id="knowledge-1",
    )
    normal_dispatch.assert_not_awaited()
    fast_dispatch.assert_not_awaited()


async def test_rag_workflow_write_bypasses_normal_and_fast_dispatch(monkeypatch) -> None:
    rag_write = AsyncMock()
    normal_dispatch = AsyncMock()
    fast_dispatch = AsyncMock()
    monkeypatch.setattr(dispatcher, "write_messages_to_rag", rag_write)
    monkeypatch.setattr(dispatcher, "check_sliding_window_and_dispatch", normal_dispatch)
    monkeypatch.setattr(dispatcher, "safe_push_fast_write", fast_dispatch)
    messages = [
        {"role": "user", "content": "remember me", "should_memorize": True},
        {"role": "assistant", "content": "okay", "should_memorize": True},
    ]

    await dispatcher.ingest_workflow_messages(
        messages=messages,
        conversation_id="conversation-1",
        end_user_id="end-user-1",
        config_id="config-1",
        workspace_id="workspace-1",
        storage_type="rag",
        user_rag_memory_id="knowledge-1",
    )

    rag_write.assert_awaited_once_with(
        messages=messages,
        end_user_id="end-user-1",
        user_rag_memory_id="knowledge-1",
    )
    normal_dispatch.assert_not_awaited()
    fast_dispatch.assert_not_awaited()


async def test_rag_api_write_bypasses_normal_and_fast_dispatch(monkeypatch) -> None:
    rag_write = AsyncMock()
    normal_dispatch = AsyncMock()
    fast_dispatch = AsyncMock()
    monkeypatch.setattr(dispatcher, "write_messages_to_rag", rag_write)
    monkeypatch.setattr(dispatcher, "push_write_task", normal_dispatch)
    monkeypatch.setattr(dispatcher, "safe_push_fast_write", fast_dispatch)
    messages = [{"role": "user", "content": "remember me"}]

    task_ids = await dispatcher.dispatch_api_service_async(
        messages=messages,
        end_user_id="end-user-1",
        config_id="config-1",
        workspace_id="workspace-1",
        storage_type="rag",
        user_rag_memory_id="knowledge-1",
    )

    assert task_ids == []
    rag_write.assert_awaited_once_with(
        messages=messages,
        end_user_id="end-user-1",
        user_rag_memory_id="knowledge-1",
    )
    normal_dispatch.assert_not_awaited()
    fast_dispatch.assert_not_awaited()


async def test_rag_mcp_write_bypasses_normal_and_fast_dispatch(monkeypatch) -> None:
    rag_write = AsyncMock()
    normal_dispatch = AsyncMock()
    fast_dispatch = AsyncMock()
    monkeypatch.setattr(dispatcher, "write_messages_to_rag", rag_write)
    monkeypatch.setattr(dispatcher, "push_write_task", normal_dispatch)
    monkeypatch.setattr(dispatcher, "safe_push_fast_write", fast_dispatch)

    task_id = await dispatcher.dispatch_mcp_write(
        message="remember me",
        end_user_id="end-user-1",
        config_id="config-1",
        workspace_id="workspace-1",
        storage_type="rag",
        user_rag_memory_id="knowledge-1",
        dialog_at="2026-09-23T00:00:00+00:00",
    )

    assert task_id == ""
    rag_write.assert_awaited_once_with(
        messages=[{
            "role": "user",
            "content": "remember me",
            "dialog_at": "2026-09-23T00:00:00+00:00",
        }],
        end_user_id="end-user-1",
        user_rag_memory_id="knowledge-1",
    )
    normal_dispatch.assert_not_awaited()
    fast_dispatch.assert_not_awaited()


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


async def test_fast_write_embeds_dialogue_as_document() -> None:
    embedder = Mock()
    embedder.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
    embedder.aembed_query = AsyncMock(
        side_effect=AssertionError("dialogue content must not use query embedding")
    )
    pipeline = FastWritePipeline(Mock(), "user-1")
    pipeline._embedder = embedder

    result = await pipeline._embed("hello")

    assert result == [0.1, 0.2]
    embedder.aembed_documents.assert_awaited_once_with(["hello"])
    embedder.aembed_query.assert_not_awaited()


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
