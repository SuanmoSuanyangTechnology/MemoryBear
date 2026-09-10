"""Memory value-ranking mutations routed through MemoryStorageService."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.models import NodeFilter, StorageWriteResult
from app.repositories.neo4j.cypher_queries import (
    PERMANENT_MEMORY_ID_BY_ELEMENT_ID,
    PERMANENT_MEMORY_LIST,
)
from app.services.memory_value_ranking_service import (
    MemoryValueRankingService,
    PermanentMemoryNotFound,
)


def test_permanent_memory_list_keeps_element_id_contract() -> None:
    assert "elementId(s) AS id" in PERMANENT_MEMORY_LIST


def _write_result(*node_ids: str) -> StorageWriteResult:
    return StorageWriteResult(
        backend=BackendType.NEO4J,
        affected_count=len(node_ids),
        ids=list(node_ids),
    )


def _connector(used: int = 0, node_id: str = "statement-1") -> Mock:
    async def execute_query(query: str, **kwargs):
        if query == PERMANENT_MEMORY_ID_BY_ELEMENT_ID:
            return [{"node_id": node_id}]
        return [{"used": used}]

    return Mock(
        execute_query=AsyncMock(side_effect=execute_query),
        close=AsyncMock(),
    )


async def test_unmark_updates_statement_by_business_id_and_end_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    end_user_id = "00000000-0000-0000-0000-000000000001"
    connector = _connector(used=2)
    storage = Mock(
        update_node=AsyncMock(return_value=_write_result("statement-1")),
    )
    invalidated: list[str] = []
    monkeypatch.setattr(
        "app.services.memory_value_ranking_service.get_total_memory_limit",
        AsyncMock(return_value=300),
    )
    monkeypatch.setattr(
        "app.services.memory_value_ranking_service.invalidate_cache",
        AsyncMock(side_effect=lambda *, prefix: invalidated.append(prefix)),
    )
    service = MemoryValueRankingService(
        Mock(),
        connector_factory=lambda: connector,
        storage_service=storage,
    )
    service._get_end_user = AsyncMock(return_value=SimpleNamespace(id=end_user_id))

    result = await service.unmark_permanent_memory(
        "4:db:1",
        end_user_id,
        Mock(),
    )

    storage.update_node.assert_awaited_once_with(
        MemoryNodeType.STATEMENT,
        {"is_permanent": False},
        NodeFilter.all_of(
            NodeFilter.eq("id", "statement-1"),
            NodeFilter.eq("end_user_id", end_user_id),
        ),
    )
    assert result.id == "4:db:1"
    assert result.is_permanent is False
    assert result.quota.used == 2
    assert invalidated == [
        f"forget_candidates:{end_user_id}",
        f"permanent_memories:{end_user_id}",
    ]
    connector.close.assert_awaited_once_with()


async def test_unmark_reports_not_found_when_storage_updates_no_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    end_user_id = "00000000-0000-0000-0000-000000000001"
    connector = _connector(node_id="")
    storage = Mock(update_node=AsyncMock(return_value=_write_result()))
    monkeypatch.setattr(
        "app.services.memory_value_ranking_service.get_total_memory_limit",
        AsyncMock(return_value=300),
    )
    service = MemoryValueRankingService(
        Mock(),
        connector_factory=lambda: connector,
        storage_service=storage,
    )
    service._get_end_user = AsyncMock(return_value=SimpleNamespace(id=end_user_id))

    with pytest.raises(PermanentMemoryNotFound, match="statement not found"):
        await service.unmark_permanent_memory("missing", end_user_id, Mock())

    connector.execute_query.assert_awaited_once_with(
        PERMANENT_MEMORY_ID_BY_ELEMENT_ID,
        element_id="missing",
        end_user_id=end_user_id,
    )
    connector.close.assert_awaited_once_with()
