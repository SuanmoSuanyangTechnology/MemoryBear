from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from neo4j.time import DateTime

from app.core.memory.models.graph_models import PreferenceNode
from app.core.memory.storage.custom.end_user_merge import _parse_preference_snapshot
from app.core.memory.storage.custom.preference import (
    create_preference_if_absent,
    get_preference,
    update_preference,
)
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage_services.preference_engine.models import PreferenceItem


async def test_update_preference_enqueues_projection(monkeypatch):
    node = PreferenceNode(
        id="preference-1",
        end_user_id="user-1",
        domain="coding",
        subject="code_style",
        situation_key="global",
        mode=["positive"],
        preference_text=["use type hints"],
        created_at=datetime(2026, 9, 29),
        updated_at=datetime(2026, 9, 29),
    )
    connector = Mock(
        execute_write_transaction=AsyncMock(
            return_value={"node": node.model_dump()}
        )
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "app.core.memory.storage.custom.preference.enqueue_events",
        enqueue,
    )

    saved = await update_preference(
        connector,
        node,
        [PreferenceItem(mode="positive", preference_text="use type hints")],
    )

    assert saved == node
    event = enqueue.await_args.args[0][0]
    assert event.label is MemoryNodeType.PREFERENCE
    assert event.node_id == node.id


class _Result:
    def __init__(self, row):
        self.row = row

    async def single(self, *, strict=False):
        return Mock(data=lambda: self.row)


class _Transaction:
    def __init__(self, row):
        self.row = row
        self.query = ""

    async def run(self, query, **kwargs):
        self.query = query
        self.parameters = kwargs
        return _Result(self.row)


class _Connector:
    def __init__(self, tx):
        self.tx = tx

    async def execute_write_transaction(self, callback):
        return await callback(self.tx)


def _preference_node():
    return PreferenceNode(
        id="preference-1",
        end_user_id="user-1",
        domain="coding",
        subject="code_style",
        situation_key="global",
        mode=["positive"],
        preference_text=["use type hints"],
        created_at=datetime(2026, 9, 29),
        updated_at=datetime(2026, 9, 29),
    )


async def test_create_preference_uses_dialogue_timestamp_type(monkeypatch):
    node = _preference_node()
    tx = _Transaction({"node": node.model_dump(), "created": True})
    connector = _Connector(tx)
    monkeypatch.setattr(
        "app.core.memory.storage.custom.preference.enqueue_events",
        AsyncMock(),
    )

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    await create_preference_if_absent(
        connector,
        end_user_id=node.end_user_id,
        domain=node.domain,
        subject=node.subject,
        situation_key=node.situation_key,
        items=[PreferenceItem(mode="positive", preference_text="use type hints")],
    )

    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert before <= tx.parameters["created_at"] <= after
    assert tx.parameters["created_at"].tzinfo is None
    assert tx.parameters["updated_at"] == tx.parameters["created_at"]
    assert "n.created_at = $created_at" in tx.query
    assert "n.updated_at = $updated_at" in tx.query


async def test_update_preference_uses_dialogue_timestamp_type(monkeypatch):
    node = _preference_node()
    tx = _Transaction({"node": node.model_dump()})
    connector = _Connector(tx)
    monkeypatch.setattr(
        "app.core.memory.storage.custom.preference.enqueue_events",
        AsyncMock(),
    )

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    await update_preference(
        connector,
        node,
        [PreferenceItem(mode="positive", preference_text="use type hints")],
    )

    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert before <= tx.parameters["updated_at"] <= after
    assert tx.parameters["updated_at"].tzinfo is None
    assert "n.updated_at = $updated_at" in tx.query
    assert "n.created_at" not in tx.query


@pytest.mark.parametrize("tzinfo", [None, timezone.utc])
@pytest.mark.parametrize("operation", ["read", "create", "update"])
async def test_preference_reads_neo4j_timestamps_as_python_datetime(
    monkeypatch, tzinfo, operation
):
    node = _preference_node()
    created_at = DateTime(2026, 9, 28, 12, 0, 0, tzinfo=tzinfo)
    updated_at = DateTime(2026, 9, 29, 13, 0, 0, tzinfo=tzinfo)
    properties = node.model_dump() | {
        "created_at": created_at,
        "updated_at": updated_at,
    }
    connector = _Connector(_Transaction({"node": properties, "created": True}))
    connector.execute_query = AsyncMock(return_value=[{"node": properties}])
    monkeypatch.setattr(
        "app.core.memory.storage.custom.preference.enqueue_events", AsyncMock()
    )
    key = {
        "end_user_id": node.end_user_id,
        "domain": node.domain,
        "subject": node.subject,
        "situation_key": node.situation_key,
    }
    items = [PreferenceItem(mode="positive", preference_text="use type hints")]

    if operation == "read":
        saved = await get_preference(connector, **key)
    elif operation == "create":
        saved, created = await create_preference_if_absent(connector, **key, items=items)
        assert created
    else:
        saved = await update_preference(connector, node, items)

    assert isinstance(saved.created_at, datetime)
    assert isinstance(saved.updated_at, datetime)
    assert saved.created_at == created_at.to_native()
    assert saved.updated_at == updated_at.to_native()


@pytest.mark.parametrize("tzinfo", [None, timezone.utc])
def test_preference_snapshot_converts_neo4j_timestamps(tzinfo):
    node = _preference_node()
    timestamp = DateTime(2026, 9, 29, 12, 0, 0, tzinfo=tzinfo)
    rows = [{
        "element_id": "element-1",
        "properties": node.model_dump() | {
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    }]

    buckets = _parse_preference_snapshot(
        rows, owner="source end user", end_user_id=node.end_user_id
    )

    assert isinstance(buckets[0].node.created_at, datetime)
    assert isinstance(buckets[0].node.updated_at, datetime)
    assert buckets[0].node.created_at == timestamp.to_native()
    assert buckets[0].node.updated_at == timestamp.to_native()
