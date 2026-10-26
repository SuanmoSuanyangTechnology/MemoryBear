"""Cube-specific behaviour: error mapping, event folding, addressing, configuration."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from src.backends import (
    META_KEY,
    STATE_PAUSED,
    STATE_RUNNING,
    BackendError,
    BackendUnavailable,
    CapacityExhausted,
    ContextGone,
    InstanceGone,
)
from src.backends.cube import backend as backend_module
from src.backends.cube.backend import KERNEL_DIED, fold_events
from src.backends.cube.config import CubeConfig, load_config
from src.envconf import ConfigError
from tests.backends.cube.fake_cube import API_URL, PROXY_ADDR, boom, reply
from tests.contract.backend_contract import new_instance, spec

# -- fold_events -----------------------------------------------------------------------


def test_fold_collects_streams_and_main_result():
    result = fold_events(
        [
            {"type": "number_of_executions", "execution_count": 1},
            {"type": "stdout", "text": "a"},
            {"type": "stderr", "text": "w\n"},
            {"type": "stdout", "text": "b\n"},
            {"type": "result", "is_main_result": True, "text": "2"},
            {"type": "end_of_execution"},
        ],
        duration_ms=7,
    )
    assert result.stdout == "ab\n2\n"
    assert result.stderr == "w\n"
    assert result.error is None
    assert result.duration_ms == 7


def test_fold_ignores_display_results_that_are_not_the_main_result():
    result = fold_events([{"type": "result", "is_main_result": False, "text": "x"}], 0)
    assert result.stdout == ""


def test_fold_reports_the_first_error_and_strips_ansi_from_the_traceback():
    result = fold_events(
        [
            {
                "type": "error",
                "name": "ValueError",
                "value": "boom",
                "traceback": "\x1b[31mT\x1b[0m",
            },
            {"type": "error", "name": "KeyError", "value": "k", "traceback": ""},
        ],
        0,
    )
    assert result.error == "ValueError: boom"
    assert result.stderr == "T\n"


def test_fold_maps_a_dropped_execution_to_kernel_died():
    result = fold_events(
        [{"type": "error", "name": "UnexpectedEndOfExecution", "value": "x", "traceback": ""}], 0
    )
    assert result.error == KERNEL_DIED


def test_fold_error_without_value_uses_the_name_only():
    assert fold_events([{"type": "error", "name": "Boom"}], 0).error == "Boom"


# -- lifecycle ---------------------------------------------------------------------------


async def test_create_sends_template_never_timeout_and_metadata(make_backend, cube):
    backend = make_backend()
    handle = await backend.create(spec("d9", "tenant-x"))
    (request,) = cube.requests_to("POST", r"^/sandboxes$")
    body = json.loads(request.content)
    assert body["templateID"] == "workspace"
    assert body["timeout"] == -1
    assert body["metadata"][META_KEY] == "d9"
    assert handle.instance_id in cube.sandboxes


async def test_create_conflict_means_capacity_exhausted(make_backend, cube):
    cube.capacity = 0
    with pytest.raises(CapacityExhausted):
        await make_backend().create(spec())


async def test_create_server_error_is_unavailable(make_backend, cube):
    cube.intercept("POST", r"^/sandboxes$", reply(500, {"message": "x"}))
    with pytest.raises(BackendUnavailable):
        await make_backend().create(spec())


async def test_create_without_an_id_is_a_plain_backend_error(make_backend, cube):
    cube.intercept("POST", r"^/sandboxes$", reply(201, {}))
    with pytest.raises(BackendError) as excinfo:
        await make_backend().create(spec())
    assert excinfo.type is BackendError


async def test_unreachable_control_plane_is_unavailable_and_does_not_leak_the_address(
    make_backend, cube
):
    cube.intercept("POST", r"^/sandboxes$", boom(httpx.ConnectError("connect failed")))
    with pytest.raises(BackendUnavailable) as excinfo:
        await make_backend().create(spec())
    assert "cube-api" not in str(excinfo.value)


async def test_resume_uses_connect_with_no_expiry(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    await backend.pause(instance_id)
    await backend.resume(instance_id)
    (request,) = cube.requests_to("POST", r"/connect$")
    assert json.loads(request.content) == {"timeout": -1}
    assert not cube.requests_to("POST", r"/resume$")


async def test_resume_conflict_means_capacity_exhausted(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    cube.intercept("POST", r"/connect$", reply(409, {"message": "no room"}))
    with pytest.raises(CapacityExhausted):
        await backend.resume(instance_id)


async def test_destroy_while_the_sandbox_is_busy_is_unavailable(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    cube.intercept("DELETE", r"^/sandboxes/", reply(503, {"message": "busy"}, {"Retry-After": "2"}))
    with pytest.raises(BackendUnavailable):
        await backend.destroy(instance_id)
    await backend.destroy(instance_id)  # the retry goes through


async def test_ids_are_percent_encoded_in_the_path(make_backend, cube):
    with pytest.raises(InstanceGone):
        await make_backend().destroy("a/b")
    (request,) = cube.requests_to("DELETE", r".*")
    assert request.url.raw_path == b"/sandboxes/a%2Fb"


# -- listing -------------------------------------------------------------------------------


async def test_list_owned_keeps_only_marked_sandboxes_and_maps_states(make_backend, cube):
    backend = make_backend()
    mine = await new_instance(backend)
    cube.add_foreign(None)
    cube.add_foreign({"team": "other"})
    pausing = cube.add_foreign({META_KEY: "p"}, state="pausing")
    cube.add_foreign({META_KEY: "k"}, state="killing")
    infos = {i.instance_id: i for i in await backend.list_owned()}
    assert set(infos) == {mine, pausing}
    assert infos[mine].state == STATE_RUNNING
    assert infos[pausing].state == STATE_PAUSED


async def test_list_owned_sends_the_page_size_and_warns_when_the_page_is_full(
    make_backend, cube, caplog
):
    backend = make_backend(list_limit=2)
    await new_instance(backend, "a")
    await new_instance(backend, "b")
    with caplog.at_level(logging.WARNING):
        infos = await backend.list_owned()
    assert len(infos) == 2
    (request,) = cube.requests_to("GET", r"^/v2/sandboxes$")
    assert request.url.params["limit"] == "2"
    assert "SANDBOX_CUBE_LIST_LIMIT" in caplog.text


async def test_list_owned_rejects_a_non_array_payload(make_backend, cube):
    cube.intercept("GET", r"^/v2/sandboxes$", reply(200, {"oops": 1}))
    with pytest.raises(BackendUnavailable):
        await make_backend().list_owned()


# -- code execution -----------------------------------------------------------------------


async def test_run_code_returns_streams_and_main_result(make_backend):
    backend = make_backend()
    instance_id = await new_instance(backend)
    ctx = await backend.create_context(instance_id, "python3")
    printed = await backend.run_code(instance_id, ctx, "print(1)", 5.0)
    assert (printed.stdout, printed.stderr, printed.error) == ("out\n", "warn\n", None)
    value = await backend.run_code(instance_id, ctx, "1+1", 5.0)
    assert value.stdout == "2\n"


async def test_run_code_reports_a_user_error_with_a_clean_traceback(make_backend):
    backend = make_backend()
    instance_id = await new_instance(backend)
    ctx = await backend.create_context(instance_id, "python3")
    result = await backend.run_code(instance_id, ctx, "RAISE", 5.0)
    assert result.error == "ValueError: boom"
    assert "\x1b" not in result.stderr
    assert "Traceback" in result.stderr


async def test_run_code_dropped_execution_is_kernel_died(make_backend):
    backend = make_backend()
    instance_id = await new_instance(backend)
    ctx = await backend.create_context(instance_id, "python3")
    assert (await backend.run_code(instance_id, ctx, "DIE", 5.0)).error == KERNEL_DIED


async def test_run_code_timeout_propagates_so_the_scheduler_can_restart_the_kernel(
    make_backend, cube, monkeypatch
):
    monkeypatch.setattr(backend_module, "EXEC_GRACE_S", 0.0)
    backend = make_backend()
    instance_id = await new_instance(backend)
    ctx = await backend.create_context(instance_id, "python3")

    async def slow(_request):
        await asyncio.sleep(30)

    cube.intercept("POST", r"^/execute$", slow)
    with pytest.raises(TimeoutError):
        await backend.run_code(instance_id, ctx, "print(1)", 0.05)


async def test_data_plane_connection_failure_on_a_live_sandbox_is_unavailable(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    ctx = await backend.create_context(instance_id, "python3")
    cube.intercept("POST", r"^/execute$", boom(httpx.ConnectError("refused")))
    with pytest.raises(BackendUnavailable) as excinfo:
        await backend.run_code(instance_id, ctx, "print(1)", 5.0)
    assert "cube-proxy" not in str(excinfo.value)


async def test_data_plane_server_error_on_a_live_sandbox_is_unavailable(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    cube.intercept("POST", r"^/contexts$", reply(500, {"detail": "x"}))
    with pytest.raises(BackendUnavailable):
        await backend.create_context(instance_id, "python3")


async def test_data_plane_client_error_on_a_live_sandbox_is_not_retryable(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    with pytest.raises(BackendError) as excinfo:
        await backend.create_context(instance_id, "ruby")
    assert excinfo.type is BackendError


async def test_missing_context_is_context_gone_not_instance_gone(make_backend):
    backend = make_backend()
    instance_id = await new_instance(backend)
    with pytest.raises(ContextGone):
        await backend.run_code(instance_id, "nope", "x", 5.0)


# -- addressing ---------------------------------------------------------------------------


async def test_host_mode_targets_the_proxy_with_a_per_sandbox_host_header(make_backend, cube):
    backend = make_backend()
    instance_id = await new_instance(backend)
    await backend.create_context(instance_id, "python3")
    (request,) = cube.requests_to("POST", r"^/contexts$")
    assert request.url.host == "cube-proxy"
    assert request.headers["host"] == f"49999-{instance_id}.cube.app"


async def test_path_mode_targets_the_sandbox_prefix(make_backend, cube):
    backend = make_backend(route_mode="path", code_port=4999)
    instance_id = await new_instance(backend)
    await backend.create_context(instance_id, "python3")
    (request,) = cube.requests_to("POST", r"/contexts$")
    assert request.url.path == f"/sandbox/{instance_id}/4999/contexts"


async def test_api_key_goes_to_the_control_plane_only(make_backend, cube):
    backend = make_backend(api_key="s3cret-key")
    instance_id = await new_instance(backend)
    await backend.create_context(instance_id, "python3")
    control = [r for r in cube.requests if r.url.host == "cube-api"]
    data = [r for r in cube.requests if r.url.host == "cube-proxy"]
    assert control and data
    assert all(r.headers["authorization"] == "Bearer s3cret-key" for r in control)
    assert all("authorization" not in r.headers for r in data)


async def test_no_api_key_means_no_authorization_header(make_backend, cube):
    await new_instance(make_backend())
    assert all("authorization" not in r.headers for r in cube.requests)


# -- health and shutdown ----------------------------------------------------------------


async def test_health_is_false_on_error_status_or_network_failure(make_backend, cube):
    backend = make_backend()
    assert await backend.health() is True
    cube.intercept("GET", r"^/health$", reply(500, {"message": "down"}))
    assert await backend.health() is False
    cube.intercept("GET", r"^/health$", boom(httpx.ConnectError("refused")))
    assert await backend.health() is False


async def test_aclose_is_safe(make_backend):
    backend = make_backend()
    await backend.aclose()


def test_native_ttl_is_unset_because_the_scheduler_owns_the_lifetime(make_backend):
    assert make_backend().native_ttl_s is None


# -- configuration --------------------------------------------------------------------------

ENV = {"SANDBOX_CUBE_API_URL": API_URL, "SANDBOX_CUBE_PROXY_ADDR": PROXY_ADDR}


def test_config_defaults_and_overrides():
    cfg = load_config(ENV)
    assert (cfg.api_url, cfg.proxy_addr) == (API_URL, PROXY_ADDR)
    assert (cfg.route_mode, cfg.code_port, cfg.api_key) == ("host", 49999, None)
    cfg = load_config({**ENV, "SANDBOX_CUBE_ROUTE_MODE": "path", "SANDBOX_CUBE_CODE_PORT": "4999"})
    assert (cfg.route_mode, cfg.code_port) == ("path", 4999)


@pytest.mark.parametrize("missing", ["SANDBOX_CUBE_API_URL", "SANDBOX_CUBE_PROXY_ADDR"])
def test_config_names_the_missing_required_variable(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_config(env)


def test_config_names_every_missing_variable_at_once():
    with pytest.raises(ConfigError) as excinfo:
        load_config({})
    assert "SANDBOX_CUBE_API_URL" in str(excinfo.value)
    assert "SANDBOX_CUBE_PROXY_ADDR" in str(excinfo.value)


@pytest.mark.parametrize(
    "over",
    [
        {"SANDBOX_CUBE_API_URL": "cube-api:3000"},
        {"SANDBOX_CUBE_PROXY_ADDR": "http://proxy:8080"},
        {"SANDBOX_CUBE_ROUTE_MODE": "dns"},
        {"SANDBOX_CUBE_PROXY_SCHEME": "ftp"},
        {"SANDBOX_CUBE_CODE_PORT": "0"},
        {"SANDBOX_CUBE_HTTP_TIMEOUT_S": "0"},
        {"SANDBOX_CUBE_LIST_LIMIT": "0"},
        {"SANDBOX_CUBE_CODE_PORT": "abc"},
    ],
)
def test_config_rejects_bad_values(over):
    with pytest.raises(ConfigError):
        load_config({**ENV, **over})


def test_config_never_prints_the_api_key():
    cfg = CubeConfig(api_url=API_URL, proxy_addr=PROXY_ADDR, api_key="s3cret-key")
    assert "s3cret-key" not in repr(cfg)
