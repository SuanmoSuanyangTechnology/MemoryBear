"""SceneSummary persistence through the internal storage service."""

from contextlib import nullcontext
from datetime import datetime
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.core.memory.scene.scene_summary_service import SceneSummaryService
from app.core.memory.storage.enums import (
    BackendType,
    MemoryNodeType,
)
from app.core.memory.storage.models import StorageWriteResult
from app.core.memory.storage.service import MemoryStorageService
from app.schemas.scene_memory_schema import GenerateSceneSummaryTask


async def test_scene_summary_is_written_through_storage(monkeypatch) -> None:
    config = SimpleNamespace(
        scene_idle_timeout_seconds=3600,
        scene_min_chars_to_summary=0,
    )
    interval = {
        "conversation_id": "conversation-1",
        "close_reason": "SHIFTED",
        "messages": [
            {
                "id": "message-1",
                "role": "user",
                "content": "hello",
                "created_at": datetime(2026, 9, 1, 10),
            },
            {
                "id": "message-2",
                "role": "assistant",
                "content": "hi",
                "created_at": datetime(2026, 9, 1, 10, 1),
            },
        ],
    }
    storage = Mock(
        save_node=AsyncMock(
            return_value=StorageWriteResult(
                backend=BackendType.NEO4J,
                affected_count=1,
                ids=["message-1"],
            )
        ),
        close=AsyncMock(),
    )
    connector = Mock(close=AsyncMock())
    repository = Mock(get_source_message_ids=AsyncMock(return_value=None))
    repository_type = Mock(return_value=repository)
    create_storage = AsyncMock(return_value=storage)
    monkeypatch.setattr(
        MemoryStorageService,
        "create_graph_write_only",
        create_storage,
    )
    monkeypatch.setattr(
        "app.core.memory.scene.scene_summary_service.Neo4jConnector",
        lambda: connector,
    )
    monkeypatch.setattr(
        "app.core.memory.scene.scene_summary_service.SceneSummaryRepository",
        repository_type,
    )
    monkeypatch.setattr(
        "app.core.memory.scene.scene_summary_service.get_db_context",
        lambda: nullcontext(Mock()),
    )
    monkeypatch.setattr(
        "app.core.memory.scene.scene_summary_service.MemoryConfigService",
        lambda _db: SimpleNamespace(load_memory_config=lambda _id: config),
    )
    monkeypatch.setattr(
        "app.core.memory.scene.scene_summary_service.MemoryMessageRepository",
        lambda _db: SimpleNamespace(load_scene_interval=lambda **_kwargs: interval),
    )
    service = SceneSummaryService()
    service._generate_content = AsyncMock(
        return_value=("a short scene summary", [0.1, 0.2])
    )

    result = await service.generate(
        GenerateSceneSummaryTask(
            end_user_id="user-1",
            config_id="config-1",
            scene_start_message_id="message-1",
            close_before_message_id="message-3",
            close_reason="SHIFTED",
        )
    )

    assert result == {"status": "success", "summary_id": "message-1"}
    repository_type.assert_called_once_with(connector)
    repository.get_source_message_ids.assert_awaited_once_with("message-1")
    create_storage.assert_awaited_once_with()
    label, payload = storage.save_node.await_args.args
    assert label is MemoryNodeType.SCENE_SUMMARY
    expected_payload = {
        "id": "message-1",
        "end_user_id": "user-1",
        "conversation_id": "conversation-1",
        "content": "a short scene summary",
        "summary_embedding": [0.1, 0.2],
        "source_message_ids": ["message-1", "message-2"],
        "start_message_id": "message-1",
        "end_message_id": "message-2",
        "turn_count": 1,
        "close_reason": "SHIFTED",
        "config_id": "config-1",
    }
    assert {
        field: payload[field]
        for field in expected_payload
    } == expected_payload
    storage.close.assert_awaited_once_with()
    connector.close.assert_awaited_once_with()


def test_outbox_constraint_migration_allows_scene_summary(monkeypatch) -> None:
    migration = import_module(
        "migrations.versions."
        "d94f6b2a1c73_202609091200_add_scene_summary_outbox_label"
    )
    drop_constraint = Mock()
    create_check_constraint = Mock()
    monkeypatch.setattr(migration.op, "drop_constraint", drop_constraint)
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        create_check_constraint,
    )

    migration.upgrade()

    drop_constraint.assert_called_once_with(
        "ck_memory_outbox_label",
        "memory_storage_outbox_events",
        type_="check",
    )
    expression = create_check_constraint.call_args.args[2]
    assert "'SceneSummary'" in expression
