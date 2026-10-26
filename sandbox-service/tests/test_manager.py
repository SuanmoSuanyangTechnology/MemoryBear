"""SandboxManager behaviour (design doc 3.1, V11) against the in-memory backend."""

from __future__ import annotations

import asyncio

import pytest

from src.backends import (
    META_KEY,
    META_TENANT,
    BackendError,
    CapacityExhausted,
    FakeBackend,
    RunResult,
)
from src.errors import SandboxError
from src.keys import KernelKey, SandboxKey

MIN = 60.0
IDLE = 21 * MIN  # just past A=20min
HOUR = 3600.0


def sbx(user="u1", tenant="t") -> SandboxKey:
    return SandboxKey(tenant, "w", user)


def K(user="u1", conv="c1", tenant="t", lang="python3") -> KernelKey:
    return KernelKey(sbx(user, tenant), conv, lang)


async def settle(n: int = 30) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def contexts_of(env, instance_id: str) -> dict:
    return env.backend._instances[instance_id].contexts


# --------------------------------------------------------------- lazy allocation


async def test_nothing_is_allocated_until_a_sandbox_call(make_env):
    env = make_env()
    assert env.mgr.stats()["instances"] == 0
    assert env.backend.created_total == 0


async def test_invalid_requests_allocate_nothing(make_env):
    env = make_env()
    with pytest.raises(SandboxError) as e1:
        await env.mgr.execute(K(), 123)  # type: ignore[arg-type]
    assert e1.value.kind == "invalid_request"
    with pytest.raises(SandboxError) as e2:
        await env.mgr.execute(K(), "x", timeout_s=0)
    assert e2.value.kind == "invalid_request"
    assert env.backend.created_total == 0
    assert env.mgr.stats()["instances"] == 0


async def test_first_call_creates_then_hits_then_new_conversation_adds_kernel_only(make_env):
    env = make_env()
    o1 = await env.mgr.execute(K(conv="c1"), "a")
    assert o1.path == "create" and o1.result.stdout == "a"
    assert not o1.workspace_reset and not o1.kernel_reset

    o2 = await env.mgr.execute(K(conv="c1"), "b")
    assert o2.path == "hit" and o2.context_id == o1.context_id

    o3 = await env.mgr.execute(K(conv="c2"), "c")
    assert o3.path == "hit"
    assert o3.instance_id == o1.instance_id and o3.context_id != o1.context_id

    assert env.backend.created_total == 1
    assert env.backend.contexts_created_total == 2
    assert env.mgr.metrics["path.create"] == 1 and env.mgr.metrics["path.hit"] == 2


async def test_languages_get_separate_kernels(make_env):
    env = make_env()
    py = await env.mgr.execute(K(lang="python3"), "a")
    js = await env.mgr.execute(K(lang="nodejs"), "a")
    assert py.instance_id == js.instance_id and py.context_id != js.context_id


async def test_users_get_separate_instances(make_env):
    env = make_env()
    a = await env.mgr.execute(K(user="u1"), "a")
    b = await env.mgr.execute(K(user="u2"), "a")
    assert a.instance_id != b.instance_id


async def test_concurrent_first_calls_create_one_instance(make_env):
    env = make_env()
    env.backend.create_gate = asyncio.Event()
    tasks = [asyncio.create_task(env.mgr.execute(K(conv=f"c{i}"), "x")) for i in range(3)]
    await settle()
    env.backend.create_gate.set()
    outs = await asyncio.gather(*tasks)
    assert env.backend.created_total == 1
    assert sorted(o.path for o in outs) == ["create", "hit", "hit"]
    assert env.backend.contexts_created_total == 3


async def test_concurrent_calls_on_one_kernel_create_one_kernel(make_env):
    env = make_env()
    outs = await asyncio.gather(*[env.mgr.execute(K(), "x") for _ in range(4)])
    assert env.backend.created_total == 1
    assert env.backend.contexts_created_total == 1
    assert len({o.context_id for o in outs}) == 1


async def test_kernel_limit_per_user(make_env):
    env = make_env(max_kernels_per_user=2)
    await env.mgr.execute(K(conv="a"), "x")
    await env.mgr.execute(K(conv="b"), "x")
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(conv="c"), "x")
    assert ei.value.kind == "quota_exceeded" and ei.value.status_code == 402
    # Existing kernels keep working.
    assert (await env.mgr.execute(K(conv="a"), "y")).path == "hit"


async def test_kernel_limit_is_counted_per_channel(make_env):
    # Terminal kernels do not eat the code-kernel quota: with a limit of 2, two
    # conversations can each hold a code kernel AND a shell kernel (4 in total).
    env = make_env(max_kernels_per_user=2)
    env.backend.run_hook = shell_hook()
    for conv in ("a", "b"):
        await env.mgr.execute(K(conv=conv), "x")
        out = await env.mgr.run_command(sbx(), conv, "echo hi")
        assert out.verdict.status == "ok"
    # A third conversation is still refused, on either channel.
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(conv="c"), "x")
    assert ei.value.kind == "quota_exceeded"
    with pytest.raises(SandboxError) as ei:
        await env.mgr.run_command(sbx(), "c", "echo hi")
    assert ei.value.kind == "quota_exceeded"


# ------------------------------------------------------------------ TTL ladder


async def test_idle_instance_is_paused_and_resumed_on_next_call(make_env):
    env = make_env()
    o1 = await env.mgr.execute(K(), "a")
    env.clock.advance(IDLE)
    rep = await env.mgr.tick()
    assert rep.paused == 1
    assert env.backend.state_of(o1.instance_id) == "paused"

    o2 = await env.mgr.execute(K(), "b")
    assert o2.path == "resume" and o2.instance_id == o1.instance_id
    assert not o2.workspace_reset
    assert env.backend.state_of(o1.instance_id) == "running"
    assert env.backend.resumed_total == 1
    # Freshly resumed: not paused again by the very next tick.
    assert (await env.mgr.tick()).paused == 0


async def test_recent_activity_prevents_pause(make_env):
    env = make_env()
    await env.mgr.execute(K(), "a")
    env.clock.advance(15 * MIN)
    await env.mgr.execute(K(), "b")
    env.clock.advance(15 * MIN)
    assert (await env.mgr.tick()).paused == 0


async def test_retention_expiry_destroys_and_reports_workspace_reset(make_env):
    env = make_env(max_retention_s=HOUR, absolute_lifetime_s=10 * HOUR)
    o1 = await env.mgr.execute(K(), "a")
    env.clock.advance(IDLE)
    await env.mgr.tick()  # paused
    env.clock.advance(HOUR + 1)
    rep = await env.mgr.tick()
    assert rep.expired == 1
    assert env.backend.state_of(o1.instance_id) is None

    o2 = await env.mgr.execute(K(), "b")
    assert o2.path == "create" and o2.workspace_reset
    # The marker is reported once.
    assert not (await env.mgr.execute(K(), "c")).workspace_reset


async def test_absolute_lifetime_forces_recreate(make_env):
    env = make_env(absolute_lifetime_s=HOUR, idle_pause_s=10 * HOUR)
    o1 = await env.mgr.execute(K(), "a")
    env.clock.advance(HOUR + 1)
    rep = await env.mgr.tick()
    assert rep.lifetime_expired == 1
    o2 = await env.mgr.execute(K(), "b")
    assert o2.path == "create" and o2.instance_id != o1.instance_id and o2.workspace_reset


async def test_tombstones_expire(make_env):
    env = make_env(absolute_lifetime_s=100, tombstone_ttl_s=1000, idle_pause_s=10 * HOUR)
    await env.mgr.execute(K(), "a")
    env.clock.advance(101)
    await env.mgr.tick()  # destroyed, tombstone written
    env.clock.advance(1001)
    await env.mgr.tick()  # tombstone pruned
    assert not (await env.mgr.execute(K(), "b")).workspace_reset


async def test_in_flight_instance_is_not_paused(make_env):
    env = make_env()
    release = asyncio.Event()

    async def hook(instance_id, context_id, code):
        await release.wait()
        return None

    env.backend.run_hook = hook
    task = asyncio.create_task(env.mgr.execute(K(), "x"))
    await settle()
    env.clock.advance(HOUR)
    rep = await env.mgr.tick()
    assert rep.paused == 0
    release.set()
    out = await task
    assert out.path == "create"
    assert (await env.mgr.tick()).paused == 0  # activity was stamped at completion


# ---------------------------------------------------- admission and eviction (3.1)


async def test_pressure_evicts_longest_paused_first_and_marks_reset(make_env):
    env = make_env(capacity=2, low_water=0.5, high_water=0.9)
    await env.mgr.execute(K(user="u1"), "a")
    env.clock.advance(IDLE)
    await env.mgr.tick()  # u1 paused first
    await env.mgr.execute(K(user="u2"), "a")
    env.clock.advance(IDLE)
    await env.mgr.tick()  # u2 paused second
    env.clock.advance(2 * HOUR + 5 * MIN)  # both past P_min

    o3 = await env.mgr.execute(K(user="u3"), "a")
    assert not o3.workspace_reset
    reg = env.mgr.registry
    assert reg.get(sbx("u1").digest) is None
    assert reg.get(sbx("u2").digest) is not None
    assert reg.has_tombstone(sbx("u1").digest)
    assert env.backend.destroyed_total == 1

    # u1 comes back: the next-longest paused instance (u2) makes room.
    o1 = await env.mgr.execute(K(user="u1"), "b")
    assert o1.path == "create" and o1.workspace_reset
    assert reg.get(sbx("u2").digest) is None
    assert env.mgr.metrics["evicted"] == 2


async def test_running_instance_is_never_evicted_so_new_user_gets_sandbox_busy(make_env):
    env = make_env(capacity=1)
    await env.mgr.execute(K(user="u1"), "a")
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(user="u2"), "a")
    err = ei.value
    assert err.kind == "sandbox_busy" and err.retryable
    assert err.retry_after_s and err.retry_after_s > 0
    assert env.mgr.registry.count() == 1
    assert env.mgr.stats()["queue_depth"] == 0
    assert env.mgr.metrics["busy"] == 1
    assert env.backend.destroyed_total == 0


async def test_queue_waits_about_queue_wait_s_before_busy(make_env):
    env = make_env(capacity=1, queue_wait_s=3.0)
    await env.mgr.execute(K(user="u1"), "a")
    started = env.clock.time()
    with pytest.raises(SandboxError):
        await env.mgr.execute(K(user="u2"), "a")
    waited = env.clock.time() - started
    assert 3.0 <= waited < 3.5


async def test_paused_instance_inside_p_min_is_protected(make_env):
    env = make_env(capacity=1)
    await env.mgr.execute(K(user="u1"), "a")
    env.clock.advance(IDLE)
    await env.mgr.tick()
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(user="u2"), "a")
    assert ei.value.kind == "sandbox_busy"
    assert env.mgr.registry.get(sbx("u1").digest) is not None

    env.clock.advance(2 * HOUR + MIN)  # now past P_min
    assert (await env.mgr.execute(K(user="u2"), "a")).path == "create"
    assert env.mgr.registry.get(sbx("u1").digest) is None


async def test_last_slot_goes_to_exactly_one_of_two_concurrent_creators(make_env):
    env = make_env(capacity=1)
    env.backend.create_gate = asyncio.Event()
    t1 = asyncio.create_task(env.mgr.execute(K(user="u1"), "a"))
    await settle()
    t2 = asyncio.create_task(env.mgr.execute(K(user="u2"), "a"))
    with pytest.raises(SandboxError) as ei:
        await t2
    assert ei.value.kind == "sandbox_busy"
    env.backend.create_gate.set()
    assert (await t1).path == "create"
    assert env.backend.created_total == 1


async def test_tenant_capacity_is_enforced_per_tenant(make_env):
    env = make_env(capacity=10, tenant_capacity=1)
    await env.mgr.execute(K(user="u1", tenant="A"), "x")
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(user="u2", tenant="A"), "x")
    assert ei.value.kind == "quota_exceeded"
    assert (await env.mgr.execute(K(user="u3", tenant="B"), "x")).path == "create"


async def test_tenant_over_capacity_evicts_its_own_long_paused_instance(make_env):
    env = make_env(capacity=10, tenant_capacity=1)
    await env.mgr.execute(K(user="u1", tenant="A"), "x")
    env.clock.advance(IDLE)
    await env.mgr.tick()
    env.clock.advance(2 * HOUR + MIN)
    out = await env.mgr.execute(K(user="u2", tenant="A"), "x")
    assert out.path == "create"
    assert env.mgr.registry.get(sbx("u1", "A").digest) is None


async def test_create_rate_limit_per_user(make_env):
    env = make_env(create_max_per_window=2, create_window_s=60.0)
    for _ in range(2):
        await env.mgr.execute(K(), "x")
        assert await env.mgr.destroy_user(sbx())
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(), "x")
    assert ei.value.kind == "rate_limited" and ei.value.status_code == 429
    assert ei.value.retry_after_s and ei.value.retry_after_s > 0
    env.clock.advance(61)
    assert (await env.mgr.execute(K(), "x")).path == "create"


# ------------------------------------------------------------- backend failures


class FullBackend(FakeBackend):
    async def create(self, spec):
        raise CapacityExhausted("no room")


class FlakyBackend(FakeBackend):
    def __init__(self, clock=None):
        super().__init__(clock)
        self.refusals = 2

    async def create(self, spec):
        if self.refusals > 0:
            self.refusals -= 1
            raise CapacityExhausted("no room")
        return await super().create(spec)


async def test_backend_out_of_room_becomes_sandbox_busy(make_env):
    env = make_env(backend_cls=FullBackend)
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(), "x")
    assert ei.value.kind == "sandbox_busy"
    assert env.mgr.registry.count() == 0
    assert env.mgr.stats()["queue_depth"] == 0


async def test_backend_out_of_room_is_retried_within_the_queue_window(make_env):
    env = make_env(backend_cls=FlakyBackend)
    out = await env.mgr.execute(K(), "x")
    assert out.path == "create" and env.backend.refusals == 0


async def test_create_failure_releases_the_slot(make_env):
    env = make_env(capacity=1)
    env.backend.fail["create"] = 1
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(), "x")
    assert ei.value.kind == "backend_unavailable" and ei.value.retryable
    assert env.mgr.registry.count() == 0
    assert (await env.mgr.execute(K(), "x")).path == "create"


async def test_lost_kernel_is_rebuilt_and_the_code_still_runs(make_env):
    env = make_env()
    o1 = await env.mgr.execute(K(), "a")
    env.backend.lose_context(o1.instance_id, o1.context_id)
    o2 = await env.mgr.execute(K(), "b")
    assert o2.kernel_reset and o2.context_id != o1.context_id
    assert o2.result.stdout == "b" and not o2.workspace_reset


async def test_lost_instance_fails_once_then_recreates_with_reset_marker(make_env):
    env = make_env()
    o1 = await env.mgr.execute(K(), "a")
    env.backend.lose_instance(o1.instance_id)
    with pytest.raises(SandboxError) as ei:
        await env.mgr.execute(K(), "b")
    assert ei.value.kind == "instance_lost" and ei.value.retryable
    o2 = await env.mgr.execute(K(), "c")
    assert o2.path == "create" and o2.workspace_reset


async def test_timeout_restarts_the_kernel_and_reports_timeout(make_env, monkeypatch):
    monkeypatch.setattr("src.services.manager.RUN_GRACE_S", 0.0)
    env = make_env()

    async def hang(instance_id, context_id, code):
        await asyncio.sleep(10)

    env.backend.run_hook = hang
    out = await env.mgr.execute(K(), "x", timeout_s=0.05)
    assert out.result.error == "timeout" and out.kernel_reset

    env.backend.run_hook = None
    again = await env.mgr.execute(K(), "y")
    assert again.result.stdout == "y" and again.context_id == out.context_id


# ----------------------------------------------------- conversation and revoke


async def test_close_conversation_drops_only_its_kernels(make_env):
    env = make_env()
    a = await env.mgr.execute(K(conv="A"), "x")
    b = await env.mgr.execute(K(conv="B"), "x")
    assert await env.mgr.close_conversation(sbx(), "A") == 1
    assert set(contexts_of(env, a.instance_id)) == {b.context_id}
    assert (await env.mgr.execute(K(conv="B"), "y")).context_id == b.context_id
    assert await env.mgr.close_conversation(sbx(), "A") == 0
    assert await env.mgr.close_conversation(sbx("nobody"), "A") == 0


async def test_close_conversation_on_paused_instance_is_flushed_on_resume(make_env):
    env = make_env()
    a = await env.mgr.execute(K(conv="A"), "x")
    env.clock.advance(IDLE)
    await env.mgr.tick()
    assert await env.mgr.close_conversation(sbx(), "A") == 1
    rec = env.mgr.registry.get(sbx().digest)
    assert rec.stale_contexts == [a.context_id]

    b = await env.mgr.execute(K(conv="B"), "x")
    assert b.path == "resume"
    assert rec.stale_contexts == []
    assert set(contexts_of(env, b.instance_id)) == {b.context_id}


async def test_destroy_user_leaves_no_reset_marker(make_env):
    env = make_env()
    await env.mgr.execute(K(), "a")
    assert await env.mgr.destroy_user(sbx()) is True
    assert await env.mgr.destroy_user(sbx()) is False
    assert env.backend.destroyed_total == 1
    out = await env.mgr.execute(K(), "b")
    assert out.path == "create" and not out.workspace_reset


# ------------------------------------------------------------------- reconcile


async def test_reconcile_destroys_old_orphans_only(make_env):
    env = make_env()
    live = await env.mgr.execute(K(), "a")
    now = env.clock.now_ms()
    meta = {META_KEY: "ghost", META_TENANT: "t"}
    old = env.backend.inject_orphan(meta, now - 10 * 60_000)
    fresh = env.backend.inject_orphan(meta, now)
    foreign = env.backend.inject_orphan({}, now - 10 * 60_000)

    rep = await env.mgr.reconcile()
    assert rep.orphans_destroyed == 1 and rep.lost == 0
    ids = set(env.backend.instance_ids())
    assert old not in ids
    assert {live.instance_id, fresh, foreign} <= ids


async def test_reconcile_forgets_instances_the_backend_lost(make_env):
    env = make_env()
    o1 = await env.mgr.execute(K(), "a")
    env.clock.advance(200)  # past the orphan grace period
    env.backend.lose_instance(o1.instance_id)
    rep = await env.mgr.reconcile()
    assert rep.lost == 1
    assert env.mgr.registry.get(sbx().digest) is None
    assert (await env.mgr.execute(K(), "b")).workspace_reset


# ------------------------------------------------------------ terminal commands


def shell_hook(exit_code=0, stdout="", stderr="", timed_out=False):
    """Fake kernel that answers a shell wrapper the way the real one would."""
    import json
    import re

    async def hook(_instance_id, _context_id, code):
        found = re.search(r"@@SBX-[0-9a-f]{16}@@", code)
        if found is None:  # plain code call: let the fake echo it
            return None
        marker = found.group(0)
        res = {"exit_code": exit_code, "stdout": stdout, "stderr": stderr,
               "timed_out": timed_out, "error": None}
        return RunResult(stdout=marker + json.dumps(res))

    return hook


async def test_run_command_reports_exit_code_on_a_separate_kernel(make_env):
    env = make_env()
    env.backend.run_hook = shell_hook(stdout="hi\n")
    code_out = await env.mgr.execute(K(conv="c1"), "x")
    out = await env.mgr.run_command(sbx(), "c1", "echo hi")
    assert out.exit_code == 0 and out.verdict.status == "ok"
    assert out.result.stdout == "hi\n"
    assert out.context_id != code_out.context_id  # shell kernel is its own
    assert out.instance_id == code_out.instance_id and out.path == "hit"
    assert len(contexts_of(env, out.instance_id)) == 2
    assert not out.kernel_reset


async def test_run_command_nonzero_exit_is_an_error_verdict(make_env):
    env = make_env()
    env.backend.run_hook = shell_hook(exit_code=3, stderr="boom\n")
    out = await env.mgr.run_command(sbx(), "c1", "false")
    assert out.exit_code == 3 and out.verdict.status == "error"
    assert out.verdict.stderr == "boom\n"


async def test_run_command_timeout_reported_by_wrapper(make_env):
    env = make_env()
    env.backend.run_hook = shell_hook(exit_code=None, timed_out=True)
    out = await env.mgr.run_command(sbx(), "c1", "sleep 999", timeout_s=5)
    assert out.verdict.status == "timeout" and out.exit_code is None


async def test_run_command_without_wrapper_output_is_an_error(make_env):
    env = make_env()  # default fake echoes the code: no marker result
    out = await env.mgr.run_command(sbx(), "c1", "echo hi")
    assert out.verdict.status == "error" and out.exit_code is None


@pytest.mark.parametrize(
    "kwargs",
    [{"cmd": ""}, {"cmd": "a\x00b"}, {"cmd": "ls", "cwd": "/etc"},
     {"cmd": "ls", "cwd": "/workspace/../etc"}, {"cmd": "ls", "cwd": "rel"},
     {"cmd": "ls", "env": {"1BAD": "x"}}, {"cmd": "ls", "timeout_s": 0}],
)
async def test_run_command_rejects_bad_input_and_allocates_nothing(make_env, kwargs):
    env = make_env()
    cmd = kwargs.pop("cmd")
    with pytest.raises(SandboxError) as ei:
        await env.mgr.run_command(sbx(), "c1", cmd, **kwargs)
    assert ei.value.kind == "invalid_request"
    assert env.backend.created_total == 0


async def test_close_conversation_also_drops_the_shell_kernel(make_env):
    env = make_env()
    env.backend.run_hook = shell_hook()
    o = await env.mgr.execute(K(conv="A"), "x")
    await env.mgr.run_command(sbx(), "A", "ls")
    assert len(contexts_of(env, o.instance_id)) == 2
    assert await env.mgr.close_conversation(sbx(), "A") == 2
    assert contexts_of(env, o.instance_id) == {}


# ------------------------------------------------------------ idle kernel reaping


async def test_idle_kernel_is_deleted_by_tick_but_the_instance_stays_running(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=3600)
    o = await env.mgr.execute(K(conv="A"), "x")
    env.clock.advance(901)
    rep = await env.mgr.tick()
    assert rep.kernels_reaped == 1 and rep.paused == 0
    assert contexts_of(env, o.instance_id) == {}
    assert env.mgr.registry.get(sbx().digest).kernels == {}
    again = await env.mgr.execute(K(conv="A"), "y")  # the conversation just gets a new kernel
    assert again.path == "hit" and again.result.stdout == "y"
    assert len(contexts_of(env, o.instance_id)) == 1


async def test_using_a_kernel_keeps_it_alive(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=3600)
    o = await env.mgr.execute(K(conv="A"), "x")
    env.clock.advance(600)
    await env.mgr.execute(K(conv="A"), "x")
    env.clock.advance(600)  # 1200s since creation, only 600s since last use
    rep = await env.mgr.tick()
    assert rep.kernels_reaped == 0
    assert len(contexts_of(env, o.instance_id)) == 1


async def test_only_the_idle_conversation_loses_its_kernel(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=3600)
    o = await env.mgr.execute(K(conv="A"), "x")
    await env.mgr.execute(K(conv="B"), "x")
    env.clock.advance(600)
    await env.mgr.execute(K(conv="B"), "x")
    env.clock.advance(400)  # A idle 1000s, B idle 400s
    rep = await env.mgr.tick()
    assert rep.kernels_reaped == 1
    assert len(contexts_of(env, o.instance_id)) == 1
    assert [k.conversation_id for k in env.mgr.registry.get(sbx().digest).kernels.values()] == ["B"]


async def test_kernel_idle_zero_disables_reaping(make_env):
    env = make_env(kernel_idle_s=0, idle_pause_s=3600)
    o = await env.mgr.execute(K(conv="A"), "x")
    env.clock.advance(3000)
    rep = await env.mgr.tick()
    assert rep.kernels_reaped == 0
    assert len(contexts_of(env, o.instance_id)) == 1


async def test_reaping_does_not_refresh_instance_activity(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=1200)
    o = await env.mgr.execute(K(conv="A"), "x")
    rec = env.mgr.registry.get(sbx().digest)
    last_active = rec.last_active_ms
    env.clock.advance(1000)
    assert (await env.mgr.tick()).kernels_reaped == 1
    assert rec.last_active_ms == last_active  # housekeeping must not keep the VM awake
    env.clock.advance(250)  # now past the 1200s pause line
    assert (await env.mgr.tick()).paused == 1
    assert env.backend.state_of(o.instance_id) == "paused"


async def test_resume_resets_kernel_idle_clock(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=300)
    o = await env.mgr.execute(K(conv="A"), "x")
    await env.mgr.execute(K(conv="B"), "x")
    env.clock.advance(400)
    assert (await env.mgr.tick()).paused == 1
    env.clock.advance(5000)  # paused for a long time
    resumed = await env.mgr.execute(K(conv="A"), "x")
    assert resumed.path == "resume"
    rep = await env.mgr.tick()  # B was not used since resume, but paused time is not idle time
    assert rep.kernels_reaped == 0
    assert len(contexts_of(env, o.instance_id)) == 2


async def test_shell_kernel_runs_the_reaper_before_it_is_deleted(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=3600)
    seen: list[str] = []
    base = shell_hook()

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return await base(instance_id, context_id, code)

    env.backend.run_hook = hook
    o = await env.mgr.run_command(sbx(), "A", "server &")
    await env.mgr.execute(K(conv="A"), "x")  # code kernel of the same conversation
    seen.clear()
    env.clock.advance(1000)
    rep = await env.mgr.tick()
    assert rep.kernels_reaped == 2
    reapers = [c for c in seen if "_sbx_reap" in c]
    assert len(reapers) == 1  # only the shell kernel gets a reaper
    assert contexts_of(env, o.instance_id) == {}


async def test_reaper_failure_does_not_block_deleting_the_shell_kernel(make_env):
    env = make_env(kernel_idle_s=900, idle_pause_s=3600)
    base = shell_hook()

    async def hook(instance_id, context_id, code):
        if "_sbx_reap" in code:
            raise BackendError("reaper blew up")
        return await base(instance_id, context_id, code)

    env.backend.run_hook = hook
    o = await env.mgr.run_command(sbx(), "A", "server &")
    env.clock.advance(1000)
    assert (await env.mgr.tick()).kernels_reaped == 1
    assert contexts_of(env, o.instance_id) == {}


async def test_close_conversation_runs_the_reaper_for_the_shell_kernel(make_env):
    env = make_env()
    seen: list[str] = []
    base = shell_hook()

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return await base(instance_id, context_id, code)

    env.backend.run_hook = hook
    await env.mgr.run_command(sbx(), "A", "server &")
    seen.clear()
    assert await env.mgr.close_conversation(sbx(), "A") == 1
    assert sum("_sbx_reap" in c for c in seen) == 1


async def test_code_kernel_recreated_after_idle_reap_reports_kernel_reset(make_env):
    env = make_env(kernel_idle_s=60)
    await env.mgr.execute(K(), "a")
    env.clock.advance(61)
    rep = await env.mgr.tick()
    assert rep.kernels_reaped == 1
    first = await env.mgr.execute(K(), "b")
    assert first.kernel_reset  # variables are gone, the model must be told
    second = await env.mgr.execute(K(), "c")
    assert not second.kernel_reset  # reported once only


async def test_other_conversations_do_not_inherit_a_reap_marker(make_env):
    env = make_env(kernel_idle_s=60)
    await env.mgr.execute(K(conv="c1"), "a")
    env.clock.advance(61)
    await env.mgr.tick()
    assert not (await env.mgr.execute(K(conv="c2"), "b")).kernel_reset


# ----------------------------------------- code kernel starts in its conversation dir


def _chdir_calls(seen: list[str]) -> list[str]:
    return [c for c in seen if "os.chdir" in c]


async def test_code_kernel_moves_into_the_conversation_dir_before_user_code(make_env):
    env = make_env()
    seen: list[str] = []

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return None

    env.backend.run_hook = hook
    out = await env.mgr.execute(K(conv="A"), "print(1)")
    assert out.result.stdout == "print(1)"  # the user's reply is not the init's
    assert seen[-1] == "print(1)"
    chdir = _chdir_calls(seen)
    assert len(chdir) == 1 and seen.index(chdir[0]) < seen.index("print(1)")
    assert "/workspace/sessions/A" in chdir[0]


async def test_chdir_runs_once_per_kernel_not_per_call(make_env):
    env = make_env()
    seen: list[str] = []

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return None

    env.backend.run_hook = hook
    await env.mgr.execute(K(conv="A"), "a")
    await env.mgr.execute(K(conv="A"), "b")
    assert len(_chdir_calls(seen)) == 1
    await env.mgr.execute(K(conv="B"), "c")  # another conversation has its own kernel
    chdir = _chdir_calls(seen)
    assert len(chdir) == 2 and "/workspace/sessions/B" in chdir[1]


async def test_shell_kernel_is_not_given_a_chdir_init(make_env):
    env = make_env()
    seen: list[str] = []
    base = shell_hook()

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return await base(instance_id, context_id, code)

    env.backend.run_hook = hook
    await env.mgr.run_command(sbx(), "A", "ls")
    assert _chdir_calls(seen) == []  # commands carry their own cwd


async def test_nodejs_kernel_is_not_given_a_python_chdir(make_env):
    env = make_env()
    seen: list[str] = []

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return None

    env.backend.run_hook = hook
    await env.mgr.execute(K(conv="A", lang="nodejs"), "1")
    assert seen == ["1"]


async def test_chdir_is_applied_again_after_a_timeout_restart(make_env, monkeypatch):
    monkeypatch.setattr("src.services.manager.RUN_GRACE_S", 0.0)
    env = make_env()
    seen: list[str] = []

    async def hook(instance_id, context_id, code):
        seen.append(code)
        if code == "hang":
            await asyncio.sleep(10)
        return None

    env.backend.run_hook = hook
    await env.mgr.execute(K(conv="A"), "a")
    out = await env.mgr.execute(K(conv="A"), "hang", timeout_s=0.05)
    assert out.result.error == "timeout"
    assert len(_chdir_calls(seen)) == 1
    await env.mgr.execute(K(conv="A"), "b")  # restarted kernel is back in its default dir
    assert len(_chdir_calls(seen)) == 2


async def test_chdir_is_applied_again_after_an_idle_reap(make_env):
    env = make_env(kernel_idle_s=60)
    seen: list[str] = []

    async def hook(instance_id, context_id, code):
        seen.append(code)
        return None

    env.backend.run_hook = hook
    await env.mgr.execute(K(conv="A"), "a")
    env.clock.advance(61)
    assert (await env.mgr.tick()).kernels_reaped == 1
    await env.mgr.execute(K(conv="A"), "b")
    assert len(_chdir_calls(seen)) == 2


async def test_a_failing_chdir_never_blocks_the_user_code(make_env):
    env = make_env()

    async def hook(instance_id, context_id, code):
        if "os.chdir" in code:
            raise BackendError("init blew up")
        return None

    env.backend.run_hook = hook
    out = await env.mgr.execute(K(conv="A"), "print(1)")
    assert out.result.stdout == "print(1)" and out.verdict.status == "ok"
