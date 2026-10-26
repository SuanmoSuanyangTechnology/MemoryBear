from __future__ import annotations

import httpx
import pytest

from src.api import create_app, load_api_token
from src.backends import RunResult
from src.config import ConfigError

TOKEN = "unit-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def body(**over):
    base = {
        "tenant_id": "t",
        "workspace_id": "w",
        "end_user_id": "u1",
        "conversation_id": "c1",
        "code": "print(1)",
    }
    base.update(over)
    return base


@pytest.fixture
def make_client(make_env):
    def _make(**cfg):
        env = make_env(**cfg)
        app = create_app(env.mgr, api_token=TOKEN)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sandbox"
        )
        return env, client

    return _make


# -- auth ----------------------------------------------------------------------------


async def test_health_needs_no_token(make_client):
    _env, client = make_client()
    async with client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert resp.json()["data"] == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token-0123456789"}, {"Authorization": TOKEN}],
)
async def test_internal_routes_reject_missing_or_wrong_token(make_client, headers):
    env, client = make_client()
    async with client:
        resp = await client.post("/internal/execute", json=body(), headers=headers)
        stats = await client.get("/internal/stats", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["data"]["kind"] == "unauthorized"
    assert stats.status_code == 401
    assert env.backend.created_total == 0  # an unauthenticated call allocates nothing


async def test_auth_is_checked_before_body_validation(make_client):
    _env, client = make_client()
    async with client:
        resp = await client.post("/internal/execute", json={"nonsense": 1})
    assert resp.status_code == 401


# -- execute -------------------------------------------------------------------------


async def test_execute_returns_envelope_with_verdict(make_client):
    _env, client = make_client()
    async with client:
        resp = await client.post("/internal/execute", json=body(code="hello"), headers=AUTH)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    assert payload["error"] == ""
    data = payload["data"]
    assert data["status"] == "ok"
    assert data["stdout"] == "hello"
    assert data["path"] == "create"
    assert data["workspace_reset"] is False


async def test_second_call_hits_the_same_instance(make_client):
    env, client = make_client()
    async with client:
        first = (await client.post("/internal/execute", json=body(), headers=AUTH)).json()
        second = (await client.post("/internal/execute", json=body(), headers=AUTH)).json()
    assert second["data"]["path"] == "hit"
    assert second["data"]["instance_id"] == first["data"]["instance_id"]
    assert env.backend.created_total == 1


async def test_user_program_error_is_a_200_with_error_status(make_client):
    env, client = make_client()

    async def hook(_i, _c, _code):
        return RunResult(stderr="Traceback...", error="ZeroDivisionError: division by zero")

    env.backend.run_hook = hook
    async with client:
        resp = await client.post("/internal/execute", json=body(), headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "error"


@pytest.mark.parametrize(
    "override",
    [{"language": "ruby"}, {"timeout_s": 0}, {"tenant_id": " "}, {"code": None}],
)
async def test_invalid_values_are_422_and_allocate_nothing(make_client, override):
    env, client = make_client()
    async with client:
        resp = await client.post("/internal/execute", json=body(**override), headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["code"] == 11001
    assert env.backend.created_total == 0


async def test_unknown_and_missing_fields_are_rejected(make_client):
    _env, client = make_client()
    async with client:
        extra = await client.post("/internal/execute", json=body(extra=1), headers=AUTH)
        missing = await client.post(
            "/internal/execute", json={"tenant_id": "t", "workspace_id": "w"}, headers=AUTH
        )
    for resp in (extra, missing):
        assert resp.status_code == 422
        assert resp.json()["data"]["kind"] == "invalid_request"
    assert "extra" in extra.json()["data"]["params"]["fields"]


async def test_full_house_returns_503_sandbox_busy_with_retry_after(make_client):
    _env, client = make_client(capacity=1, queue_wait_s=0.0)
    async with client:
        ok = await client.post("/internal/execute", json=body(), headers=AUTH)
        busy = await client.post(
            "/internal/execute", json=body(end_user_id="u2"), headers=AUTH
        )
    assert ok.status_code == 200
    assert busy.status_code == 503
    assert busy.json()["data"]["kind"] == "sandbox_busy"
    assert busy.json()["data"]["retryable"] is True
    assert "retry-after" in busy.headers


# -- commands ------------------------------------------------------------------------


def cmd_body(**over):
    base = {
        "tenant_id": "t",
        "workspace_id": "w",
        "end_user_id": "u1",
        "conversation_id": "c1",
        "cmd": "echo hi",
    }
    base.update(over)
    return base


def _shell_hook(exit_code=0, stdout="hi\n"):
    import json
    import re

    async def hook(_i, _c, code):
        found = re.search(r"@@SBX-[0-9a-f]{16}@@", code)
        if found is None:
            return None
        res = {"exit_code": exit_code, "stdout": stdout, "stderr": "",
               "timed_out": False, "error": None}
        return RunResult(stdout=found.group(0) + json.dumps(res))

    return hook


async def test_commands_returns_exit_code_and_output(make_client):
    env, client = make_client()
    env.backend.run_hook = _shell_hook(exit_code=2, stdout="out\n")
    async with client:
        resp = await client.post("/internal/commands", json=cmd_body(), headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["exit_code"] == 2 and data["status"] == "error"
    assert data["stdout"] == "out\n" and data["path"] == "create"


async def test_commands_requires_token_and_rejects_bad_input(make_client):
    env, client = make_client()
    async with client:
        denied = await client.post("/internal/commands", json=cmd_body())
        bad_cwd = await client.post(
            "/internal/commands", json=cmd_body(cwd="/etc"), headers=AUTH
        )
        extra = await client.post(
            "/internal/commands", json=cmd_body(stdin="x"), headers=AUTH
        )
    assert denied.status_code == 401
    assert bad_cwd.status_code == 422 and extra.status_code == 422
    assert env.backend.created_total == 0


# -- other routes --------------------------------------------------------------------


async def test_close_conversation_and_destroy_user(make_client):
    env, client = make_client()
    async with client:
        await client.post("/internal/execute", json=body(), headers=AUTH)
        closed = await client.post(
            "/internal/close-conversation",
            json={
                "tenant_id": "t",
                "workspace_id": "w",
                "end_user_id": "u1",
                "conversation_id": "c1",
            },
            headers=AUTH,
        )
        gone = await client.post(
            "/internal/destroy-user",
            json={"tenant_id": "t", "workspace_id": "w", "end_user_id": "u1"},
            headers=AUTH,
        )
        again = await client.post(
            "/internal/destroy-user",
            json={"tenant_id": "t", "workspace_id": "w", "end_user_id": "u1"},
            headers=AUTH,
        )
    assert closed.json()["data"] == {"closed_kernels": 1}
    assert gone.json()["data"] == {"destroyed": True}
    assert again.json()["data"] == {"destroyed": False}
    assert env.backend.instance_ids() == []


async def test_stats_reports_instances(make_client):
    _env, client = make_client()
    async with client:
        await client.post("/internal/execute", json=body(), headers=AUTH)
        resp = await client.get("/internal/stats", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"]["instances"] == 1


# -- token loading -------------------------------------------------------------------


def test_load_api_token_accepts_a_long_enough_secret():
    assert load_api_token({"SANDBOX_API_TOKEN": f"  {TOKEN}  "}) == TOKEN


@pytest.mark.parametrize("env", [{}, {"SANDBOX_API_TOKEN": ""}, {"SANDBOX_API_TOKEN": "short"}])
def test_load_api_token_refuses_missing_or_short_secret(env):
    with pytest.raises(ConfigError):
        load_api_token(env)


# -- commands ------------------------------------------------------------------------


def command_body(**over):
    base = {"tenant_id": "t", "workspace_id": "w", "end_user_id": "u1",
            "conversation_id": "c1", "cmd": "echo hi"}
    base.update(over)
    return base


async def test_commands_returns_exit_code(make_client):
    import json
    import re

    env, client = make_client()

    async def hook(_i, _c, code):
        marker = re.search(r"@@SBX-[0-9a-f]{16}@@", code).group(0)
        return RunResult(stdout=marker + json.dumps(
            {"exit_code": 2, "stdout": "o", "stderr": "e", "timed_out": False, "error": None}))

    env.backend.run_hook = hook
    async with client:
        resp = await client.post("/internal/commands", json=command_body(), headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["exit_code"] == 2 and data["status"] == "error"
    assert data["stdout"] == "o" and data["stderr"] == "e"


async def test_commands_needs_token_and_validates(make_client):
    env, client = make_client()
    async with client:
        noauth = await client.post("/internal/commands", json=command_body())
        bad = await client.post(
            "/internal/commands", json=command_body(cwd="/etc"), headers=AUTH
        )
        extra = await client.post(
            "/internal/commands", json=command_body(shell="zsh"), headers=AUTH
        )
    assert noauth.status_code == 401
    assert bad.status_code == 422 and bad.json()["data"]["kind"] == "invalid_request"
    assert extra.status_code == 422
    assert env.backend.created_total == 0
