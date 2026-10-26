"""Every tool call must end with a result the model can read (never hang or vanish)."""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

try:  # pragma: no cover - environment dependent
    import datrie  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("datrie")
    _stub.Trie = lambda *a, **k: {}
    sys.modules["datrie"] = _stub

from src.models.tool_model import ToolType
from src.schemas.tool_schema import ParameterType, ToolParameter, ToolResult
from src.tools.base import BaseTool


class _DummyTool(BaseTool):
    """Configurable tool: sleeps, raises, or returns whatever the test asks for."""

    def __init__(self, *, sleep_s: float = 0.0, raises: Exception | None = None,
                 returns="__ok__", timeout_s: float = 5.0):
        super().__init__("dummy-id", {})
        self._sleep_s = sleep_s
        self._raises = raises
        self._returns = returns
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "dummy_tool"

    @property
    def description(self) -> str:
        return "dummy"

    @property
    def tool_type(self) -> ToolType:
        return ToolType.BUILTIN

    @property
    def parameters(self):
        return [ToolParameter(name="x", type=ParameterType.STRING, description="x", required=False)]

    def resolve_execution_timeout(self, parameters) -> float:
        return self._timeout_s

    async def execute(self, **kwargs):
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        if self._raises is not None:
            raise self._raises
        if self._returns == "__ok__":
            return ToolResult.success_result(data={"ok": True}, execution_time=0.0)
        return self._returns


@pytest.mark.asyncio
async def test_hanging_tool_is_cut_off_with_timeout_error():
    tool = _DummyTool(sleep_s=10, timeout_s=0.05)
    result = await tool.safe_execute()
    assert result.success is False
    assert result.error_code == "TOOL_TIMEOUT"
    assert "dummy_tool" in result.error


@pytest.mark.asyncio
async def test_tool_returning_none_becomes_error_result():
    result = await _DummyTool(returns=None).safe_execute()
    assert result.success is False
    assert result.error_code == "EMPTY_RESULT"


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_result_even_without_message():
    result = await _DummyTool(raises=RuntimeError()).safe_execute()
    assert result.success is False
    assert result.error_code == "EXECUTION_ERROR"
    assert result.error == "RuntimeError"  # empty message falls back to the type name


@pytest.mark.asyncio
async def test_normal_tool_result_is_untouched():
    result = await _DummyTool().safe_execute()
    assert result.success is True
    assert result.data == {"ok": True}


@pytest.mark.asyncio
async def test_langchain_wrapper_returns_error_json_instead_of_raising():
    from src.tools.langchain_adapter import LangchainToolWrapper

    tool = _DummyTool()

    async def boom(**kwargs):
        raise RuntimeError("boom")

    tool.safe_execute = boom  # simulate a failure that escapes safe_execute
    wrapper = LangchainToolWrapper(tool_instance=tool)
    out = await wrapper._arun(x="1")
    payload = json.loads(out)
    assert payload["success"] is False
    assert payload["error_code"] == "EXECUTION_ERROR"
    assert "boom" in payload["error"]


@pytest.mark.asyncio
async def test_langchain_wrapper_reports_timeout_to_the_model():
    from src.tools.langchain_adapter import LangchainToolWrapper

    wrapper = LangchainToolWrapper(tool_instance=_DummyTool(sleep_s=10, timeout_s=0.05))
    payload = json.loads(await wrapper._arun(x="1"))
    assert payload["success"] is False
    assert payload["error_code"] == "TOOL_TIMEOUT"


class _FakeTrace:
    def __init__(self):
        self.finished = []

    def finish_tool(self, **kwargs):
        self.finished.append(kwargs)


def test_close_running_tool_steps_emits_tool_error_for_dangling_steps():
    from src.agent.langchain_agent import LangChainAgent

    nodes = [
        {"step_id": "a", "node_name": "sandbox_write_file", "status": "running", "started_at": 0.0},
        {"step_id": "b", "node_name": "terminal_tool", "status": "completed"},
    ]
    trace = _FakeTrace()
    events = LangChainAgent._close_running_tool_steps(nodes, trace, "agent crashed")

    assert [e["step_id"] for e in events] == ["a"]
    assert events[0]["type"] == "tool_error"
    assert events[0]["name"] == "sandbox_write_file"
    assert events[0]["error"] == "agent crashed"
    assert nodes[0]["status"] == "failed"
    assert nodes[1]["status"] == "completed"  # finished steps are left alone
    assert trace.finished and trace.finished[0]["status"] == "error"


def test_close_running_tool_steps_survives_trace_failure():
    from src.agent.langchain_agent import LangChainAgent

    class _BadTrace:
        def finish_tool(self, **kwargs):
            raise RuntimeError("trace down")

    nodes = [{"step_id": "a", "node_name": "t", "status": "running"}]
    events = LangChainAgent._close_running_tool_steps(nodes, _BadTrace(), "x")
    assert len(events) == 1 and nodes[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_required_argument_is_returned_to_the_model_as_tool_result():
    """Argument validation fails before the tool body runs; the model must still get a result."""
    from src.tools.builtin.sandbox_file_tools import SandboxWriteFileTool
    from src.tools.langchain_adapter import LangchainToolWrapper

    wrapper = LangchainToolWrapper(tool_instance=SandboxWriteFileTool("t", {}))
    message = await wrapper.ainvoke(
        {"name": wrapper.name, "args": {"content": "x" * 50}, "id": "call-1", "type": "tool_call"}
    )
    payload = json.loads(message.content)
    assert payload["success"] is False
    assert payload["error_code"] == "INVALID_ARGUMENTS"
    assert "path" in payload["error"]
    assert message.status == "error"


@pytest.mark.asyncio
async def test_cluster_reference_uses_history_passed_by_workflow():
    from src.services.multi_agent_orchestrator import MultiAgentOrchestrator

    orch = MultiAgentOrchestrator.__new__(MultiAgentOrchestrator)
    orch._loop_opening_statement = None
    orch._turn_used_context_engine = False
    orch._turn_ctx_history = None
    orch._external_history = [
        {"role": "user", "content": "我叫小万"},
        {"role": "assistant", "content": "你好小万"},
    ]
    history = await orch._resolve_loop_history(None)
    assert history == orch._external_history

    # without external history and without a conversation it still degrades to empty
    orch._external_history = None

    async def _empty(_cid):
        return []

    orch._load_cluster_history = _empty
    assert await orch._resolve_loop_history(None) == []


# ---- workflow agent node (reference mode): memory switch controls history ----

def _reference_node(memory: dict):
    from src.workflow.nodes.agent.config import AgentNodeConfig
    from src.workflow.nodes.agent.node import AgentNode

    node = AgentNode({"id": "n1", "type": "agent", "config": {}}, {}, [])
    node.typed_config = AgentNodeConfig.model_validate({
        "mode": "reference",
        "reference": {"app_id": "16fa5b92-8c10-47c6-a76f-b9e639187c96", "release_policy": "current"},
        "memory": memory,
    })
    return node


def _turns(n: int) -> dict:
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"q{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    return {"messages": msgs}


def test_reference_history_empty_when_memory_disabled():
    node = _reference_node({"enable": False})
    assert node._build_reference_history(_turns(3), "now") == []


def test_reference_history_defaults_to_last_20_when_enabled_without_window():
    node = _reference_node({"enable": True, "enable_window": False, "window_size": 4})
    history = node._build_reference_history(_turns(15), "now")
    assert len(history) == 20


def test_reference_history_respects_window_size():
    node = _reference_node({"enable": True, "enable_window": True, "window_size": 4})
    history = node._build_reference_history(_turns(15), "now")
    assert [m["content"] for m in history] == ["q13", "a13", "q14", "a14"] or len(history) == 4
    assert history[-1]["role"] == "assistant"


async def test_cluster_reference_stream_forwards_supervisor_tool_events(monkeypatch):
    """Workflow agent node referencing a cluster must see the supervisor's tool events."""
    import uuid
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from src.models.app_model import AppType
    from src.services import published_agent_runner as mod

    runtime = mod.PublishedAgentRuntime(
        app=SimpleNamespace(id=uuid.uuid4(), type=AppType.MULTI_AGENT),
        release=SimpleNamespace(id=uuid.uuid4(), version=1, version_name="v1"),
        model_config=None,
    )

    @asynccontextmanager
    async def fake_db():
        yield object()

    async def fake_resolve(*_a, **_k):
        return runtime

    async def fake_cluster_stream(self, db, rt, **_k):
        yield {"type": "agent_dispatch", "data": {"agent_id": "x"}}
        yield {"type": "tool_start", "data": {"step_id": "s1", "name": "web_search_tool", "input": {"q": "a"}}}
        yield {"type": "tool_end", "data": {"step_id": "s1", "name": "web_search_tool", "output": "ok"}}
        yield {"type": "message", "data": {"content": "hi"}}
        yield {"type": "end", "data": {}}

    monkeypatch.setattr(mod, "get_async_db_context", fake_db)
    monkeypatch.setattr(mod.PublishedAgentRunner, "_resolve_runtime", staticmethod(fake_resolve))
    monkeypatch.setattr(mod.PublishedAgentRunner, "_run_cluster_reference_stream", fake_cluster_stream)

    events = [
        e async for e in mod.PublishedAgentRunner().run_stream(
            app_id=runtime.app.id, release_policy="current", release_id=None,
            workspace_id=uuid.uuid4(), user_id=None, message="q",
            variables={}, files=[], history=[],
        )
    ]
    assert [e["type"] for e in events] == ["tool_start", "tool_end", "message", "end"]
    assert events[0]["data"]["name"] == "web_search_tool"


async def test_cluster_reference_stream_forwards_only_supervisor_agent_log(monkeypatch):
    """Supervisor iteration trace reaches the node; sub-agent traces must not replace it."""
    import uuid
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from src.models.app_model import AppType
    from src.services import published_agent_runner as mod

    runtime = mod.PublishedAgentRuntime(
        app=SimpleNamespace(id=uuid.uuid4(), type=AppType.MULTI_AGENT),
        release=SimpleNamespace(id=uuid.uuid4(), version=1, version_name="v1"),
        model_config=None,
    )

    @asynccontextmanager
    async def fake_db():
        yield object()

    async def fake_resolve(*_a, **_k):
        return runtime

    async def fake_cluster_stream(self, db, rt, **_k):
        yield {"type": "agent_log", "data": {"iterations": [1, 2]}}
        yield {"type": "agent_log", "data": {"agent_id": "sub", "iterations": [1]}}
        yield {"type": "agent_log", "data": {"agent_name": "sub", "iterations": [1]}}
        yield {"type": "message", "data": {"content": "hi"}}
        yield {"type": "end", "data": {}}

    monkeypatch.setattr(mod, "get_async_db_context", fake_db)
    monkeypatch.setattr(mod.PublishedAgentRunner, "_resolve_runtime", staticmethod(fake_resolve))
    monkeypatch.setattr(mod.PublishedAgentRunner, "_run_cluster_reference_stream", fake_cluster_stream)

    events = [
        e async for e in mod.PublishedAgentRunner().run_stream(
            app_id=runtime.app.id, release_policy="current", release_id=None,
            workspace_id=uuid.uuid4(), user_id=None, message="q",
            variables={}, files=[], history=[],
        )
    ]
    assert [e["type"] for e in events] == ["agent_log", "message", "end"]
    assert events[0]["data"] == {"iterations": [1, 2]}
