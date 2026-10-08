"""SceneSummary persistence through the internal storage service."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.core.memory.scene.scene_summary_service import SceneSummaryService
from app.schemas.scene_memory_schema import GenerateSceneSummaryTask
from app.schemas.scene_memory_schema import SceneMessage
from app.schemas.scene_community_schema import SceneValueSummaryOutput


async def test_generate_content_embeds_summary_as_document(monkeypatch) -> None:
    parsed = SceneValueSummaryOutput(
        decision="ELIGIBLE",
        topic_scope="greeting follow-up",
        summary="a short scene summary",
        reason_code="STABLE_RECALLABLE_MATTER",
        short_reason="worth following",
        confidence="HIGH",
    )
    structured = SimpleNamespace(ainvoke=AsyncMock(return_value=parsed))
    llm = SimpleNamespace(with_structured_output=Mock(return_value=structured))
    embedder = SimpleNamespace(
        aembed_documents=AsyncMock(return_value=[[0.1, 0.2]])
    )
    service = SceneSummaryService(writer=Mock())
    monkeypatch.setattr(
        service,
        "_build_clients",
        Mock(return_value=(llm, embedder)),
    )

    result, embedding = await service._generate_content(
        [
            SceneMessage(
                id="message-1",
                role="user",
                content="hello",
                created_at=datetime(2026, 9, 1, 10),
            )
        ],
        SimpleNamespace(),
    )

    assert result is parsed
    assert embedding == [0.1, 0.2]
    embedder.aembed_documents.assert_awaited_once_with([parsed.summary])


async def test_scene_summary_is_written_through_storage(monkeypatch) -> None:
    config = SimpleNamespace(
        scene_idle_timeout_seconds=3600,
        scene_min_chars_to_summary=0,
        batch_trigger_count=2,
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
    writer = Mock(
        get_scene_summary_source_ids=AsyncMock(return_value=None),
        create_scene_summary_if_absent=AsyncMock(),
        count_inactive=AsyncMock(return_value=2),
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
    service = SceneSummaryService(writer=writer)
    service._generate_content = AsyncMock(
        return_value=(
            SceneValueSummaryOutput(
                decision="ELIGIBLE",
                topic_scope="greeting follow-up",
                summary="a short scene summary",
                reason_code="STABLE_RECALLABLE_MATTER",
                short_reason="worth following",
                confidence="HIGH",
            ),
            [0.1, 0.2],
        )
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

    assert result == {
        "status": "success",
        "summary_id": "message-1",
        "community_eligibility": "ELIGIBLE",
        "inactive_count": 2,
        "community_dispatch_required": True,
    }
    writer.get_scene_summary_source_ids.assert_awaited_once_with(
        "message-1", "user-1"
    )
    payload = writer.create_scene_summary_if_absent.await_args.args[0]
    expected_payload = {
        "id": "message-1",
        "end_user_id": "user-1",
        "conversation_id": "conversation-1",
        "content": "a short scene summary",
        "topic_scope": "greeting follow-up",
        "summary_embedding": [0.1, 0.2],
        "source_message_ids": ["message-1", "message-2"],
        "start_message_id": "message-1",
        "end_message_id": "message-2",
        "turn_count": 1,
        "close_reason": "SHIFTED",
        "config_id": "config-1",
        "community_eligibility": "ELIGIBLE",
        "community_status": "INACTIVE",
    }
    assert {
        field: payload[field]
        for field in expected_payload
    } == expected_payload
    storage.close.assert_awaited_once_with()
    connector.close.assert_awaited_once_with()


async def test_existing_scene_summary_is_not_regenerated_when_sources_change(
    monkeypatch,
    caplog,
) -> None:
    config = SimpleNamespace(
        scene_idle_timeout_seconds=3600,
        scene_min_chars_to_summary=0,
        batch_trigger_count=2,
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
                "content": "new message",
                "created_at": datetime(2026, 9, 1, 10, 1),
            },
        ],
    }
    writer = Mock(
        get_scene_summary_source_ids=AsyncMock(return_value=["message-1"]),
        republish_scene_summary=AsyncMock(),
        create_scene_summary_if_absent=AsyncMock(),
        count_inactive=AsyncMock(return_value=1),
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
    service = SceneSummaryService(writer=writer)
    service._generate_content = AsyncMock()

    result = await service.generate(
        GenerateSceneSummaryTask(
            end_user_id="user-1",
            config_id="config-1",
            scene_start_message_id="message-1",
            close_before_message_id="message-3",
            close_reason="SHIFTED",
        )
    )

    assert result == {
        "status": "skipped",
        "reason": "unchanged",
        "summary_id": "message-1",
        "inactive_count": 1,
        "community_dispatch_required": False,
    }
    service._generate_content.assert_not_awaited()
    writer.create_scene_summary_if_absent.assert_not_awaited()
    writer.republish_scene_summary.assert_awaited_once_with("message-1", "user-1")
    assert "immutable summary source changed" in caplog.text


def test_outbox_label_constraint_migration_recreates_full_label_set(monkeypatch) -> None:
    migration = import_module(
        "migrations.versions.24eb21498a55_202609211914"
    )
    execute = Mock()
    create_check_constraint = Mock()
    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "add_column", Mock())
    monkeypatch.setattr(
        migration.op, "create_check_constraint", create_check_constraint
    )

    migration.upgrade()

    # The constraint must be dropped AND recreated, never left absent.
    drop_sql = execute.call_args.args[0]
    assert "DROP CONSTRAINT IF EXISTS ck_memory_outbox_label" in drop_sql
    expression = create_check_constraint.call_args.args[2]
    assert "'Preference'" in expression
    assert "'SceneCommunity'" in expression
    assert "'SceneSummary'" in expression


def test_single_alembic_head_matches_tracked_migration_set() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["40ceae7eb6f3"], f"unexpected heads: {heads}"


def test_outbox_constraint_allows_every_node_label() -> None:
    from app.core.memory.storage.enums import MemoryNodeType

    migration = import_module(
        "migrations.versions.24eb21498a55_202609211914"
    )
    # The constraint recreated by this migration is authoritative: it must
    # accept every production label, otherwise a valid projection event cannot
    # be enqueued.
    expression = migration._LABEL_CHECK
    for label in MemoryNodeType:
        assert f"'{label.value}'" in expression, f"{label.value} missing"

    # Downgrade must restore the pre-revision set without dropping the table's
    # other labels.
    previous = migration._PREVIOUS_LABEL_CHECK
    assert "'Preference'" not in previous
    assert "'SceneCommunity'" not in previous
    assert "'SceneSummary'" in previous
