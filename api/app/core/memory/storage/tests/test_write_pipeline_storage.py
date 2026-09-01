from unittest.mock import AsyncMock, Mock

from app.core.memory.pipelines.write_pipeline import ExtractionResult, WritePipeline
from app.core.memory.storage.models import MemoryGraphWriteCommand


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
