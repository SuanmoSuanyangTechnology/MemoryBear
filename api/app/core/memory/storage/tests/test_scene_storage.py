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
    async def run(self, query, **parameters):
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
    async def execute_write_transaction(self, operation):
        return await operation(Transaction())


def test_scene_summary_write_is_create_only():
    query = " ".join(SCENE_SUMMARY_CREATE_IF_ABSENT_WITH_IDENTITY.split())

    assert "ON CREATE SET s = $summary" in query
    assert "ON MATCH" not in query
    assert query.count("SET s = $summary") == 1


async def test_final_batch_writes_both_labels_then_enqueues_outbox(monkeypatch):
    enqueue = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.core.memory.storage.custom.scene_storage.enqueue_events",
        enqueue,
    )
    writer = SceneStorage(Client())

    identities = await writer.commit_batch(
        end_user_id="user-1",
        communities=[
            {
                "id": "community-1",
                "end_user_id": "user-1",
                "category_l1": "work_career",
            }
        ],
        assignments=[
            {
                "scene_summary_id": "scene-1",
                "scene_community_id": "community-1",
                "category_l1": "work_career",
                "updated_at": "now",
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
