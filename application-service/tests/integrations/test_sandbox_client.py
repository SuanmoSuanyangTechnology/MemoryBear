"""Unit tests for the sandbox-service client, runtime and code interpreter tool."""

from __future__ import annotations

import sys
import types

import httpx
import pytest

# The RAG tokenizer pulled in by ``src.tools.builtin`` needs ``datrie``, which is
# irrelevant here; stub it only when it cannot be imported.
try:  # pragma: no cover - environment dependent
    import datrie  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("datrie")
    _stub.Trie = lambda *a, **k: {}
    sys.modules["datrie"] = _stub

from src.integrations.sandbox.client import SandboxPrincipal, SandboxServiceClient
from src.integrations.sandbox.errors import (
    SandboxConfigurationError,
    SandboxProtocolError,
    SandboxServiceError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)
from src.integrations.sandbox.runtime import SandboxIntegrationRuntime

PRINCIPAL = SandboxPrincipal("t1", "w1", "u1")


def make_client(handler) -> SandboxServiceClient:
    return SandboxServiceClient(
        base_url="http://sandbox",
        api_token="secret-token-1234",
        timeout=httpx.Timeout(5.0),
        limits=httpx.Limits(max_connections=5),
        transport=httpx.MockTransport(handler),
    )


def ok_envelope(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "msg": "OK", "data": data, "error": "", "time": 1})


async def test_execute_sends_principal_and_bearer_token():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return ok_envelope({"status": "ok", "stdout": "2\n", "duration_ms": 7})

    client = make_client(handler)
    result = await client.execute(PRINCIPAL, conversation_id="c1", code="print(2)", timeout_s=9)
    await client.aclose()

    assert seen["auth"] == "Bearer secret-token-1234"
    assert seen["path"] == "/internal/execute"
    assert b'"end_user_id":"u1"' in seen["body"].replace(b" ", b"")
    assert b'"timeout_s":9' in seen["body"].replace(b" ", b"")
    assert result.ok and result.stdout == "2\n" and result.duration_ms == 7


async def test_error_envelope_becomes_service_error_with_retry_info():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "code": 11010,
                "msg": "sandbox_busy",
                "data": {"kind": "sandbox_busy", "retryable": True, "retry_after_s": 3},
                "error": "full",
                "time": 1,
            },
        )

    client = make_client(handler)
    with pytest.raises(SandboxServiceError) as info:
        await client.execute(PRINCIPAL, conversation_id="c1", code="x")
    await client.aclose()
    assert info.value.kind == "sandbox_busy"
    assert info.value.retryable is True
    assert info.value.retry_after_s == 3.0


async def test_transport_failures_are_mapped():
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def slow(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    c1 = make_client(refuse)
    with pytest.raises(SandboxUnavailableError):
        await c1.destroy_user(PRINCIPAL)
    await c1.aclose()

    c2 = make_client(slow)
    with pytest.raises(SandboxTimeoutError):
        await c2.close_conversation(PRINCIPAL, "c1")
    await c2.aclose()


async def test_malformed_bodies_are_protocol_errors():
    client = make_client(lambda _r: httpx.Response(200, text="not json"))
    with pytest.raises(SandboxProtocolError):
        await client.execute(PRINCIPAL, conversation_id="c1", code="x")
    await client.aclose()

    client = make_client(lambda _r: ok_envelope({"stdout": "no status"}))
    with pytest.raises(SandboxProtocolError):
        await client.execute(PRINCIPAL, conversation_id="c1", code="x")
    await client.aclose()


async def test_runtime_disabled_and_validation():
    runtime = SandboxIntegrationRuntime()
    await runtime.start(enabled=False, base_url="", api_token="")
    assert runtime.enabled is False
    with pytest.raises(SandboxConfigurationError):
        runtime.client  # noqa: B018
    assert await runtime.ready() is True

    with pytest.raises(SandboxConfigurationError):
        await runtime.start(enabled=True, base_url="not-a-url", api_token="t")
    with pytest.raises(SandboxConfigurationError):
        await runtime.start(enabled=True, base_url="http://x?y=1", api_token="t")
    with pytest.raises(SandboxConfigurationError):
        await runtime.start(enabled=True, base_url="http://x", api_token=" ")


async def test_runtime_enabled_ready_and_close():
    runtime = SandboxIntegrationRuntime(client_factory=lambda: make_client(
        lambda _r: ok_envelope({"ok": True})
    ))
    await runtime.start(enabled=True, base_url="http://sandbox", api_token="tok")
    assert runtime.enabled and await runtime.ready() is True
    await runtime.close()
    assert runtime.enabled is False


class _FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    async def execute(self, principal, **kwargs):
        self.calls.append((principal, kwargs))
        if self.error:
            raise self.error
        return self.result

    async def run_command(self, principal, **kwargs):
        self.calls.append((principal, kwargs))
        if self.error:
            raise self.error
        return self.result


def _result(**over):
    from src.integrations.sandbox.client import ExecuteResult

    base = dict(
        status="ok", stdout="42\n", stderr="", stdout_truncated=False,
        stderr_truncated=False, error=None, duration_ms=5,
        workspace_reset=False, kernel_reset=False,
    )
    base.update(over)
    return ExecuteResult(**base)


def _tool(monkeypatch, client):
    from src.tools.builtin import code_interpreter_tool as mod
    from src.integrations.sandbox import runtime as rt

    monkeypatch.setattr(rt, "get_sandbox_service_client", lambda: client)

    async def tenant(_ws):
        return "tenant-1"

    monkeypatch.setattr(mod.CodeInterpreterTool, "_resolve_tenant_id", staticmethod(tenant))
    return mod.CodeInterpreterTool("tool-1", {})


async def test_tool_passes_end_user_conversation_and_tenant(monkeypatch):
    client = _FakeClient(result=_result())
    tool = _tool(monkeypatch, client)
    tool.set_runtime_context(user_id="end-user-1", workspace_id="ws-1", conversation_id="conv-1")

    out = await tool.safe_execute(code="print(42)", timeout=10)

    assert out.success and out.data["stdout"] == "42\n"
    principal, kwargs = client.calls[0]
    assert (principal.tenant_id, principal.workspace_id, principal.end_user_id) == (
        "tenant-1", "ws-1", "end-user-1",
    )
    assert kwargs["conversation_id"] == "conv-1" and kwargs["timeout_s"] == 10.0


@pytest.mark.parametrize(
    "ctx",
    [
        {"user_id": "anonymous", "workspace_id": "w", "conversation_id": "c"},
        {"user_id": "u", "workspace_id": "", "conversation_id": "c"},
        {"user_id": "u", "workspace_id": "w"},
    ],
)
async def test_tool_refuses_missing_identity(monkeypatch, ctx):
    client = _FakeClient(result=_result())
    tool = _tool(monkeypatch, client)
    tool.set_runtime_context(**ctx)

    out = await tool.safe_execute(code="print(1)")

    assert not out.success and out.error_code == "SANDBOX_CONTEXT_MISSING"
    assert client.calls == []


async def test_tool_rejects_non_python_language_without_calling_service(monkeypatch):
    # The template's code kernel is Python-only; a stale agent config passing nodejs
    # must get a pointer to the terminal instead of a backend 400.
    client = _FakeClient(result=_result())
    tool = _tool(monkeypatch, client)
    tool.set_runtime_context(user_id="u", workspace_id="w", conversation_id="c")

    out = await tool.safe_execute(code="console.log(1)", language="nodejs")

    assert not out.success and out.error_code == "INVALID_ARGUMENT"
    assert "终端" in out.error
    assert client.calls == []
    assert "language" not in {p.name for p in tool.parameters}


async def test_tool_maps_service_errors_and_notices(monkeypatch):
    err = SandboxServiceError(
        status_code=503, code=11010, kind="sandbox_busy", message="full", retryable=True
    )
    tool = _tool(monkeypatch, _FakeClient(error=err))
    tool.set_runtime_context(user_id="u", workspace_id="w", conversation_id="c")
    out = await tool.safe_execute(code="x")
    assert not out.success and out.error_code == "SANDBOX_SANDBOX_BUSY"
    assert out.metadata["retryable"] is True

    tool = _tool(monkeypatch, _FakeClient(result=_result(status="error", kernel_reset=True)))
    tool.set_runtime_context(user_id="u", workspace_id="w", conversation_id="c")
    out = await tool.safe_execute(code="x")
    assert out.success and out.data["status"] == "error" and "notice" in out.data


# -- terminal tool / run_command -------------------------------------------------------


async def test_run_command_posts_to_commands_and_parses_exit_code():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read().replace(b" ", b"")
        return ok_envelope(
            {"status": "error", "exit_code": 3, "stdout": "o", "stderr": "e", "duration_ms": 4}
        )

    client = make_client(handler)
    result = await client.run_command(
        PRINCIPAL, conversation_id="c1", cmd="false", cwd="/workspace/x", timeout_s=7
    )
    await client.aclose()

    assert seen["path"] == "/internal/commands"
    assert b'"cmd":"false"' in seen["body"] and b'"cwd":"/workspace/x"' in seen["body"]
    assert result.exit_code == 3 and result.status == "error" and not result.ok


async def test_run_command_null_exit_code_and_malformed_response():
    client = make_client(lambda _r: ok_envelope({"status": "timeout", "exit_code": None}))
    result = await client.run_command(PRINCIPAL, conversation_id="c1", cmd="sleep 9")
    assert result.exit_code is None and result.status == "timeout"
    await client.aclose()

    client = make_client(lambda _r: ok_envelope({"stdout": "no status"}))
    with pytest.raises(SandboxProtocolError):
        await client.run_command(PRINCIPAL, conversation_id="c1", cmd="ls")
    await client.aclose()


def _cmd_result(**over):
    from src.integrations.sandbox.client import CommandResult

    base = dict(
        status="ok", exit_code=0, stdout="hi\n", stderr="", stdout_truncated=False,
        stderr_truncated=False, error=None, duration_ms=3, workspace_reset=False,
    )
    base.update(over)
    return CommandResult(**base)


def _terminal(monkeypatch, client):
    from src.tools.builtin import terminal_tool as mod
    from src.integrations.sandbox import runtime as rt

    monkeypatch.setattr(rt, "get_sandbox_service_client", lambda: client)

    async def tenant(_ws):
        return "tenant-1"

    monkeypatch.setattr(mod.TerminalTool, "_resolve_tenant_id", staticmethod(tenant))
    return mod.TerminalTool("tool-2", {})


async def test_terminal_tool_runs_command_with_identity(monkeypatch):
    client = _FakeClient(result=_cmd_result())
    tool = _terminal(monkeypatch, client)
    tool.set_runtime_context(user_id="eu-1", workspace_id="ws-1", conversation_id="conv-1")

    out = await tool.safe_execute(command="echo hi", cwd="/workspace/shared", timeout=12)

    assert out.success and out.data["exit_code"] == 0 and out.data["stdout"] == "hi\n"
    principal, kwargs = client.calls[0]
    assert principal.tenant_id == "tenant-1" and principal.end_user_id == "eu-1"
    assert kwargs["cmd"] == "echo hi" and kwargs["cwd"] == "/workspace/shared"
    assert kwargs["conversation_id"] == "conv-1" and kwargs["timeout_s"] == 12.0


async def test_terminal_tool_refuses_missing_identity_and_empty_command(monkeypatch):
    client = _FakeClient(result=_cmd_result())
    tool = _terminal(monkeypatch, client)
    tool.set_runtime_context(user_id="anonymous", workspace_id="w", conversation_id="c")
    out = await tool.safe_execute(command="ls")
    assert not out.success and out.error_code == "SANDBOX_CONTEXT_MISSING"

    tool.set_runtime_context(user_id="u", workspace_id="w", conversation_id="c")
    out = await tool.safe_execute(command="   ")
    assert not out.success and out.error_code == "INVALID_ARGUMENT"
    assert client.calls == []


async def test_terminal_tool_nonzero_exit_is_a_successful_tool_call(monkeypatch):
    tool = _terminal(
        monkeypatch,
        _FakeClient(result=_cmd_result(status="error", exit_code=2, workspace_reset=True)),
    )
    tool.set_runtime_context(user_id="u", workspace_id="w", conversation_id="c")
    out = await tool.safe_execute(command="false")
    assert out.success and out.data["exit_code"] == 2 and "notice" in out.data

    err = SandboxServiceError(
        status_code=503, code=11010, kind="sandbox_busy", message="full", retryable=True
    )
    tool = _terminal(monkeypatch, _FakeClient(error=err))
    tool.set_runtime_context(user_id="u", workspace_id="w", conversation_id="c")
    out = await tool.safe_execute(command="ls")
    assert not out.success and out.error_code == "SANDBOX_SANDBOX_BUSY"

