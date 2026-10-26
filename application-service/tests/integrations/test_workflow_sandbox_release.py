"""Conversation-less workflow runs release their sandbox kernels when they finish."""

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

from src.services import workflow_service as wf_mod
from src.services.workflow_service import WorkflowService
from src.integrations.sandbox import runtime as sandbox_runtime
from src.repositories.tool_repository import ToolRepository

WORKSPACE = uuid.uuid4()
TENANT = uuid.uuid4()
SESSION = "11111111-1111-4111-8111-111111111111"


class FakeClient:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple] = []
        self.fail = fail

    async def close_conversation(self, principal, conversation_id):
        self.calls.append((principal, conversation_id))
        if self.fail:
            raise RuntimeError("boom")
        return 2


@pytest.fixture
def wiring(monkeypatch):
    """Enabled integration, a fake client and a tenant lookup that succeeds."""
    client = FakeClient()
    monkeypatch.setattr(sandbox_runtime, "is_sandbox_integration_enabled", lambda: True)
    monkeypatch.setattr(sandbox_runtime, "get_sandbox_service_client", lambda: client)

    @asynccontextmanager
    async def fake_db():
        yield object()

    async def fake_tenant(db, workspace_id):
        return TENANT

    monkeypatch.setattr(wf_mod, "get_async_db_context", fake_db)
    monkeypatch.setattr(ToolRepository, "get_tenant_id_by_workspace_id_async", staticmethod(fake_tenant))
    return client


def test_has_tool_node():
    assert WorkflowService._has_tool_node([{"type": "start"}, {"type": "tool"}]) is True
    assert WorkflowService._has_tool_node([{"type": "start"}, {"type": "llm"}]) is False
    assert WorkflowService._has_tool_node(None) is False
    assert WorkflowService._has_tool_node(["not-a-dict"]) is False


async def test_release_closes_the_session_with_the_derived_principal(wiring):
    await WorkflowService._release_sandbox_session(WORKSPACE, "user-1", SESSION)

    assert len(wiring.calls) == 1
    principal, conversation_id = wiring.calls[0]
    assert conversation_id == SESSION
    assert (principal.tenant_id, principal.workspace_id, principal.end_user_id) == (
        str(TENANT),
        str(WORKSPACE),
        "user-1",
    )


@pytest.mark.parametrize(
    "workspace_id,user_id,session_id",
    [
        (WORKSPACE, "", SESSION),
        (WORKSPACE, None, SESSION),
        (WORKSPACE, "anonymous", SESSION),
        (WORKSPACE, "user-1", None),
        (WORKSPACE, "user-1", ""),
        (None, "user-1", SESSION),
    ],
)
async def test_release_skips_without_identity_or_session(wiring, workspace_id, user_id, session_id):
    await WorkflowService._release_sandbox_session(workspace_id, user_id, session_id)
    assert wiring.calls == []


async def test_release_skips_when_integration_disabled(wiring, monkeypatch):
    monkeypatch.setattr(sandbox_runtime, "is_sandbox_integration_enabled", lambda: False)
    await WorkflowService._release_sandbox_session(WORKSPACE, "user-1", SESSION)
    assert wiring.calls == []


async def test_release_skips_when_tenant_is_unknown(wiring, monkeypatch):
    async def no_tenant(db, workspace_id):
        return None

    monkeypatch.setattr(ToolRepository, "get_tenant_id_by_workspace_id_async", staticmethod(no_tenant))
    await WorkflowService._release_sandbox_session(WORKSPACE, "user-1", SESSION)
    assert wiring.calls == []


async def test_release_failure_never_reaches_the_run(wiring):
    wiring.fail = True
    await WorkflowService._release_sandbox_session(WORKSPACE, "user-1", SESSION)
    assert len(wiring.calls) == 1
