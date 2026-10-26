"""Tool node: which conversation id reaches the tool (real, debug fallback, or none)."""

from __future__ import annotations

import sys
import types
import uuid
from contextlib import asynccontextmanager

import pytest

try:  # pragma: no cover - environment dependent
    import datrie  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("datrie")
    _stub.Trie = lambda *a, **k: {}
    sys.modules["datrie"] = _stub

from src.tools.base import ToolResult
from src.workflow.engine.variable_pool import VariablePool
from src.workflow.nodes.tool import node as tool_node_mod
from src.workflow.variable.base_variable import VariableType

TENANT = str(uuid.uuid4())
WORKSPACE = str(uuid.uuid4())
USER = str(uuid.uuid4())


async def _pool(conversation_id: str | None, sandbox_session_id: str | None = None) -> VariablePool:
    pool = VariablePool()
    sys_vars = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "user_id": USER}
    if sandbox_session_id is not None:
        sys_vars["sandbox_session_id"] = sandbox_session_id
    if conversation_id is not None:
        sys_vars["conversation_id"] = conversation_id
    for key, value in sys_vars.items():
        await pool.new("sys", key, value, VariableType.STRING, mut=False)
    return pool


@pytest.fixture
def seen(monkeypatch):
    """Replace DB + ToolService so execute() only records what the tool would receive."""
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_db():
        yield object()

    class FakeToolService:
        def __init__(self, _db):
            pass

        async def get_tool_config_async(self, *_a, **_k):
            return {"enabled": True}

        async def get_tool_instance_async(self, *_a, **_k):
            return None

        async def execute_tool(self, **kwargs):
            calls.append(kwargs)
            return ToolResult.success_result(data="ok", execution_time=0.0)

    monkeypatch.setattr(tool_node_mod, "get_async_db_context", fake_db)
    monkeypatch.setattr(tool_node_mod, "ToolService", FakeToolService)
    return calls


def _node():
    return tool_node_mod.ToolNode(
        {
            "id": "n1",
            "type": "tool",
            "name": "sandbox import",
            "config": {"tool_id": "t1", "tool_parameters": {"file_id": "abc"}},
        },
        {},
        [],
    )


async def _run(conversation_id: str | None, state: dict, sandbox_session_id: str | None = None) -> dict:
    return await _node().execute(state, await _pool(conversation_id, sandbox_session_id))


async def test_real_conversation_id_is_passed_through(seen):
    conv = str(uuid.uuid4())
    await _run(conv, {"__debug_conversation_id": str(uuid.uuid4())})
    assert seen[0]["conversation_id"] == uuid.UUID(conv)  # debug id never overrides a real one


async def test_single_node_run_falls_back_to_debug_conversation_id(seen):
    debug = str(uuid.uuid4())
    out = await _run(None, {"__debug_conversation_id": debug})
    assert seen[0]["conversation_id"] == uuid.UUID(debug)
    assert out["data"] == "ok"


async def test_empty_real_conversation_id_also_falls_back(seen):
    debug = str(uuid.uuid4())
    await _run("", {"__debug_conversation_id": debug})
    assert seen[0]["conversation_id"] == uuid.UUID(debug)


async def test_no_conversation_at_all_stays_none(seen):
    await _run(None, {})
    assert seen[0]["conversation_id"] is None



async def test_pure_workflow_run_uses_sandbox_session_id(seen):
    sid = str(uuid.uuid4())
    await _run(None, {}, sandbox_session_id=sid)
    assert seen[0]["conversation_id"] == uuid.UUID(sid)


async def test_sandbox_session_id_never_overrides_real_or_debug_id(seen):
    conv = str(uuid.uuid4())
    debug = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    await _run(conv, {"__debug_conversation_id": debug}, sandbox_session_id=sid)
    await _run(None, {"__debug_conversation_id": debug}, sandbox_session_id=sid)
    assert seen[0]["conversation_id"] == uuid.UUID(conv)
    assert seen[1]["conversation_id"] == uuid.UUID(debug)


def test_derived_session_id_is_stable_per_app_and_user():
    from src.services.workflow_service import WorkflowService

    app_a, app_b = uuid.uuid4(), uuid.uuid4()
    derive = WorkflowService._derive_sandbox_session_id
    assert derive(app_a, "u1") == derive(app_a, "u1")  # every run reuses one directory and kernel
    assert derive(app_a, "u1") != derive(app_a, "u2")
    assert derive(app_a, "u1") != derive(app_b, "u1")
    uuid.UUID(derive(app_a, "u1"))  # a valid uuid, the tool node normalizes it
