from unittest.mock import AsyncMock, Mock

from app.core.memory.models.graph_models import PreferenceNode
from app.core.memory.storage.custom.preference import update_preference
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
