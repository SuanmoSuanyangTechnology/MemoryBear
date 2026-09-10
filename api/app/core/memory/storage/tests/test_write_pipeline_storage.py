from unittest.mock import AsyncMock, Mock

import pytest

from app.core.memory.pipelines.write_pipeline import ExtractionResult, WritePipeline
from app.core.memory.storage.models import MemoryGraphWriteCommand
from app.core.memory.storage.service import MemoryStorageService


async def test_write_pipeline_store_delegates_complete_graph_to_storage():
    pipeline = WritePipeline(memory_config=Mock(), end_user_id="user-1")
    pipeline._storage_service = Mock(save_memory_graph=AsyncMock())
    pipeline._clean_cross_role_aliases = AsyncMock()
    result = ExtractionResult(
        dialogue_nodes=[],
        chunk_nodes=[],
        statement_nodes=[],
        entity_nodes=[],
        perceptual_nodes=[],
        stmt_chunk_edges=[],
        stmt_entity_edges=[],
        entity_entity_edges=[],
        perceptual_edges=[],
    )

    assert await pipeline._store(result) is True

    command = pipeline._storage_service.save_memory_graph.await_args.args[0]
    assert isinstance(command, MemoryGraphWriteCommand)
    assert command == MemoryGraphWriteCommand()


async def test_write_pipeline_initializes_owned_graph_write_service_once(
    monkeypatch: pytest.MonkeyPatch,
):
    service = Mock(close=AsyncMock())
    create = AsyncMock(return_value=service)
    monkeypatch.setattr(
        MemoryStorageService,
        "create_graph_write_only",
        create,
    )
    pipeline = WritePipeline(memory_config=Mock(), end_user_id="user-1")

    await pipeline._init_storage_service()
    await pipeline._init_storage_service()

    create.assert_awaited_once_with()
    assert pipeline._storage_service is service

    await pipeline._cleanup()
    service.close.assert_awaited_once_with()


async def test_write_pipelines_do_not_share_storage_service(
    monkeypatch: pytest.MonkeyPatch,
):
    first = Mock(close=AsyncMock())
    second = Mock(close=AsyncMock())
    create = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(
        MemoryStorageService,
        "create_graph_write_only",
        create,
    )
    first_pipeline = WritePipeline(memory_config=Mock(), end_user_id="user-1")
    second_pipeline = WritePipeline(memory_config=Mock(), end_user_id="user-2")

    await first_pipeline._init_storage_service()
    await second_pipeline._init_storage_service()

    assert first_pipeline._storage_service is first
    assert second_pipeline._storage_service is second
    assert first_pipeline._storage_service is not second_pipeline._storage_service
    assert create.await_count == 2

    await first_pipeline._cleanup()
    await second_pipeline._cleanup()
    first.close.assert_awaited_once_with()
    second.close.assert_awaited_once_with()


async def test_write_pipeline_cleanup_closes_all_owned_resources():
    storage = Mock(close=AsyncMock())
    connector = Mock(close=AsyncMock())
    pipeline = WritePipeline(memory_config=Mock(), end_user_id="user-1")
    pipeline._storage_service = storage
    pipeline._neo4j_connector = connector

    await pipeline._cleanup()

    storage.close.assert_awaited_once_with()
    connector.close.assert_awaited_once_with()
    assert pipeline._storage_service is None
    assert pipeline._neo4j_connector is None


async def test_write_pipeline_cleanup_attempts_all_resources_on_close_errors():
    storage = Mock(close=AsyncMock(side_effect=RuntimeError("storage close")))
    connector = Mock(close=AsyncMock(side_effect=RuntimeError("connector close")))
    pipeline = WritePipeline(memory_config=Mock(), end_user_id="user-1")
    pipeline._storage_service = storage
    pipeline._neo4j_connector = connector

    await pipeline._cleanup()

    storage.close.assert_awaited_once_with()
    connector.close.assert_awaited_once_with()
    assert pipeline._storage_service is None
    assert pipeline._neo4j_connector is None
