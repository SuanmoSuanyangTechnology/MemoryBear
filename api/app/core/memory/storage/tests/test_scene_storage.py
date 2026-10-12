from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.core.memory.storage.custom.scene_storage import (
    SCENE_COMMUNITY_UPSERT_BATCH,
    SCENE_SUMMARY_ASSIGN_BATCH,
    SCENE_SUMMARY_CREATE_IF_ABSENT_WITH_IDENTITY,
    SceneStorage,
)
from app.core.memory.storage.enums import MemoryNodeType


class Statement:
    def __init__(self, rows):
        self.rows = rows

    async def data(self):
        return self.rows


class Transaction:
    def __init__(self):
        self.calls = []

    async def run(self, query, **parameters):
        self.calls.append((query, parameters))
        if query == SCENE_COMMUNITY_UPSERT_BATCH:
            rows = [
                {"element_id": f"community:{row['id']}", "node_id": row["id"]}
                for row in parameters["communities"]
            ]
        elif query == SCENE_SUMMARY_ASSIGN_BATCH:
            rows = [
                {
                    "element_id": f"scene:{row['scene_summary_id']}",
                    "node_id": row["scene_summary_id"],
                }
                for row in parameters["assignments"]
            ]
        else:
            raise AssertionError("unexpected query")
        return Statement(rows)


class Client:
    def __init__(self):
        self.transaction = Transaction()

    async def execute_write_transaction(self, operation):
        return await operation(self.transaction)


def test_scene_summary_write_is_create_only():
    query = " ".join(SCENE_SUMMARY_CREATE_IF_ABSENT_WITH_IDENTITY.split())

    assert "ON CREATE SET s = $summary" in query
    assert "ON MATCH" not in query
    assert query.count("SET s = $summary") == 1


async def test_scene_summary_timestamps_are_utc_local_datetimes(monkeypatch):
    monkeypatch.setattr(
        "app.core.memory.storage.custom.scene_storage.enqueue_events",
        AsyncMock(return_value=[]),
    )
    client = Client()
    client.execute_validated_write_query = AsyncMock(
        side_effect=lambda _query, validator, **_params: validator(
            [{"element_id": "scene:scene-1", "node_id": "scene-1"}]
        )
    )
    writer = SceneStorage(client)
    local_time = datetime(2026, 9, 1, 18, tzinfo=timezone(timedelta(hours=8)))
    summary = {
        "id": "scene-1",
        "end_user_id": "user-1",
        **{field: local_time for field in ("started_at", "ended_at", "created_at", "updated_at")},
    }

    await writer.create_scene_summary_if_absent(summary)

    saved = client.execute_validated_write_query.await_args.kwargs["summary"]
    for field in ("started_at", "ended_at", "created_at", "updated_at"):
        assert saved[field] == datetime(2026, 9, 1, 10)
        assert saved[field].tzinfo is None
    assert summary["created_at"] == local_time


async def test_scene_reads_normalize_existing_zoned_datetimes():
    local_time = datetime(2026, 9, 1, 18, tzinfo=timezone(timedelta(hours=8)))
    client = Client()
    client.execute_query = AsyncMock(return_value=[{
        "scene_summary": {
            "id": "scene-1",
            **{field: local_time for field in ("started_at", "ended_at", "created_at", "updated_at")},
        }
    }])

    rows = await SceneStorage(client).load_inactive_batch("user-1", 1)

    for field in ("started_at", "ended_at", "created_at", "updated_at"):
        assert rows[0][field] == datetime(2026, 9, 1, 10)
        assert rows[0][field].tzinfo is None


async def test_final_batch_writes_both_labels_then_enqueues_outbox(monkeypatch):
    enqueue = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.core.memory.storage.custom.scene_storage.enqueue_events",
        enqueue,
    )
    client = Client()
    writer = SceneStorage(client)
    local_time = datetime(2026, 9, 1, 18, tzinfo=timezone(timedelta(hours=8)))

    identities = await writer.commit_batch(
        end_user_id="user-1",
        communities=[
            {
                "id": "community-1",
                "end_user_id": "user-1",
                "category_l1": "work_career",
                **{field: local_time for field in ("started_at", "ended_at", "created_at", "updated_at")},
            }
        ],
        assignments=[
            {
                "scene_summary_id": "scene-1",
                "scene_community_id": "community-1",
                "category_l1": "work_career",
                "updated_at": local_time,
            }
        ],
    )

    assert {(item.label, item.node_id) for item in identities} == {
        (MemoryNodeType.SCENE_COMMUNITY, "community-1"),
        (MemoryNodeType.SCENE_SUMMARY, "scene-1"),
    }
    events = enqueue.await_args.args[0]
    assert {(event.label, event.node_id) for event in events} == {
        (MemoryNodeType.SCENE_COMMUNITY, "community-1"),
        (MemoryNodeType.SCENE_SUMMARY, "scene-1"),
    }
    community = client.transaction.calls[0][1]["communities"][0]
    assignment = client.transaction.calls[1][1]["assignments"][0]
    for field in ("started_at", "ended_at", "created_at", "updated_at"):
        assert community[field] == datetime(2026, 9, 1, 10)
        assert community[field].tzinfo is None
    assert assignment["updated_at"] == datetime(2026, 9, 1, 10)
    assert assignment["updated_at"].tzinfo is None
    assert "localdatetime(original_created_at)" in SCENE_COMMUNITY_UPSERT_BATCH
    for field in ("started_at", "ended_at", "created_at"):
        assert f"s.{field} = localdatetime(s.{field})" in SCENE_SUMMARY_ASSIGN_BATCH


async def test_final_batch_rejects_assignment_outside_transaction_communities():
    writer = SceneStorage(Client())

    try:
        await writer.commit_batch(
            end_user_id="user-1",
            communities=[{
                "id": "community-1",
                "end_user_id": "user-1",
                "category_l1": "work_career",
            }],
            assignments=[{
                "scene_summary_id": "scene-1",
                "scene_community_id": "community-2",
                "category_l1": "work_career",
            }],
        )
    except ValueError as exc:
        assert "target a community" in str(exc)
    else:
        raise AssertionError("invalid assignment was accepted")
