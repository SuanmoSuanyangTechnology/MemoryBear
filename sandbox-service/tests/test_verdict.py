from __future__ import annotations

import pytest

from src.backends.base import RunResult
from src.keys import KernelKey, SandboxKey
from src.services.verdict import (
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_OOM,
    STATUS_TIMEOUT,
    TERMINAL_STATUSES,
    OutputBudget,
    clamp_text,
    judge,
)

# --------------------------------------------------------------------- clamp_text


def test_short_text_is_untouched():
    text, total, cut = clamp_text("hello", 100)
    assert (text, total, cut) == ("hello", 5, False)


def test_exact_budget_is_not_truncated():
    text, total, cut = clamp_text("a" * 10, 10)
    assert cut is False and total == 10 and text == "a" * 10


def test_long_text_keeps_head_and_tail_with_marker():
    raw = "H" * 50 + "M" * 900 + "T" * 50
    text, total, cut = clamp_text(raw, 100)
    assert cut is True and total == 1000
    assert text.startswith("H" * 50)
    assert text.endswith("T" * 50)
    assert "[truncated 900 bytes]" in text
    assert "M" not in text


def test_kept_body_never_exceeds_budget_for_multibyte_text():
    raw = "漢字" * 500  # 3 bytes per char
    text, total, cut = clamp_text(raw, 100)
    assert cut is True and total == 3000
    body = text.split("\n...[truncated")[0] + text.split("]...\n")[1]
    assert len(body.encode("utf-8")) <= 100
    # no replacement characters from a cut inside a character
    assert "\ufffd" not in text


def test_head_ratio_extremes():
    raw = "a" * 20 + "b" * 20
    only_tail, _, _ = clamp_text(raw, 10, head_ratio=0.0)
    assert only_tail.endswith("b" * 10) and not only_tail.startswith("a")
    only_head, _, _ = clamp_text(raw, 10, head_ratio=1.0)
    assert only_head.startswith("a" * 10) and not only_head.endswith("b")


def test_budget_validation():
    with pytest.raises(ValueError):
        OutputBudget(stdout_max_bytes=0)
    with pytest.raises(ValueError):
        OutputBudget(stderr_max_bytes=0)
    with pytest.raises(ValueError):
        OutputBudget(head_ratio=1.5)


# ------------------------------------------------------------------------- judge


def test_ok():
    v = judge(RunResult(stdout="hi", duration_ms=7))
    assert v.status == STATUS_OK and v.ok and v.error is None
    assert v.stdout == "hi" and v.duration_ms == 7


def test_user_error_is_error_with_first_line_only():
    v = judge(RunResult(stderr="trace", error="ZeroDivisionError: x\nline2\nline3"))
    assert v.status == STATUS_ERROR and not v.ok
    assert v.error == "ZeroDivisionError: x"


def test_blank_error_string_still_counts_as_error():
    v = judge(RunResult(error="   "))
    assert v.status == STATUS_ERROR and v.error == STATUS_ERROR


def test_long_error_text_is_capped():
    v = judge(RunResult(error="E" * 5000))
    assert v.status == STATUS_ERROR and len(v.error or "") == 200


def test_timeout():
    v = judge(RunResult(error="timeout"))
    assert v.status == STATUS_TIMEOUT and v.error == STATUS_TIMEOUT


@pytest.mark.parametrize("raw", ["oom", "oom_killed"])
def test_oom_from_backend_error(raw):
    assert judge(RunResult(error=raw)).status == STATUS_OOM


def test_oom_from_external_signal_even_without_error_string():
    assert judge(RunResult(), oom_signal=True).status == STATUS_OOM


def test_kill_with_memory_at_limit_is_oom():
    v = judge(
        RunResult(error="killed"),
        memory_peak_bytes=980,
        memory_limit_bytes=1000,
    )
    assert v.status == STATUS_OOM


def test_kill_with_low_memory_is_plain_error():
    v = judge(
        RunResult(error="killed"),
        memory_peak_bytes=100,
        memory_limit_bytes=1000,
    )
    assert v.status == STATUS_ERROR


def test_kill_without_memory_data_is_not_guessed_as_oom():
    assert judge(RunResult(error="killed")).status == STATUS_ERROR


def test_user_printed_oom_text_does_not_trigger_oom():
    """OOM is decided from external signals, never from what the program printed."""

    v = judge(RunResult(stdout="oom_killed", stderr="Killed: out of memory"))
    assert v.status == STATUS_OK


def test_precedence_cancelled_over_oom_over_timeout_over_error():
    both = RunResult(error="timeout")
    assert judge(both, cancelled=True, oom_signal=True).status == STATUS_CANCELLED
    assert judge(both, oom_signal=True).status == STATUS_OOM
    assert judge(both).status == STATUS_TIMEOUT
    assert judge(RunResult(error="boom")).status == STATUS_ERROR


def test_every_verdict_has_exactly_one_known_terminal_status():
    cases = [
        judge(RunResult()),
        judge(RunResult(error="x")),
        judge(RunResult(error="timeout")),
        judge(RunResult(error="oom")),
        judge(RunResult(), cancelled=True),
    ]
    assert {c.status for c in cases} == TERMINAL_STATUSES


def test_stdout_and_stderr_are_clamped_independently():
    budget = OutputBudget(stdout_max_bytes=20, stderr_max_bytes=1000)
    v = judge(RunResult(stdout="x" * 500, stderr="e" * 50), budget)
    assert v.stdout_truncated is True and v.stdout_bytes == 500
    assert v.stderr_truncated is False and v.stderr == "e" * 50


def test_to_dict_has_stable_keys():
    d = judge(RunResult(stdout="a")).to_dict()
    assert set(d) == {
        "status",
        "stdout",
        "stderr",
        "stdout_bytes",
        "stderr_bytes",
        "stdout_truncated",
        "stderr_truncated",
        "error",
        "duration_ms",
    }


# ------------------------------------------------------------- manager integration


def _key() -> KernelKey:
    return KernelKey(SandboxKey("t", "w", "u1"), "c1", "python3")


async def test_manager_attaches_a_verdict_to_every_outcome(make_env):
    env = make_env()
    out = await env.mgr.execute(_key(), "print(1)")
    assert out.verdict.status == STATUS_OK
    assert out.verdict.stdout == "print(1)"  # FakeBackend echoes the code


async def test_manager_clamps_output_to_the_budget(make_env):
    from src.services import SandboxManager, SchedulerConfig

    env = make_env()
    mgr = SandboxManager(
        env.backend,
        SchedulerConfig(),
        clock=env.clock,
        budget=OutputBudget(stdout_max_bytes=64, stderr_max_bytes=64),
    )
    out = await mgr.execute(_key(), "z" * 10_000)
    assert out.verdict.stdout_truncated is True
    assert out.verdict.stdout_bytes == 10_000
    assert len(out.verdict.stdout.encode()) < 200


async def test_manager_maps_backend_oom_to_oom_status(make_env):
    env = make_env()

    async def hook(_i, _c, _code):
        return RunResult(error="oom_killed")

    env.backend.run_hook = hook
    out = await env.mgr.execute(_key(), "x")
    assert out.verdict.status == STATUS_OOM


async def test_manager_reports_user_error_status(make_env):
    env = make_env()

    async def hook(_i, _c, _code):
        return RunResult(stderr="tb", error="ValueError: bad")

    env.backend.run_hook = hook
    out = await env.mgr.execute(_key(), "x")
    assert out.verdict.status == STATUS_ERROR
    assert out.verdict.error == "ValueError: bad"


async def test_request_timeout_is_capped_by_the_service_hard_limit(make_env):
    env = make_env(exec_timeout_default_s=10.0, exec_timeout_max_s=30.0)
    seen: list[float] = []
    original = env.backend.run_code

    async def spy(instance_id, context_id, code, timeout_s):
        if "_sbx_cd" not in code:  # skip the kernel's chdir init call
            seen.append(timeout_s)
        return await original(instance_id, context_id, code, timeout_s)

    env.backend.run_code = spy  # type: ignore[method-assign]
    await env.mgr.execute(_key(), "a", timeout_s=9999)
    await env.mgr.execute(_key(), "b", timeout_s=5)
    await env.mgr.execute(_key(), "c")
    assert seen == [30.0, 5, 10.0]


def test_config_rejects_default_timeout_above_the_hard_cap():
    from src.services import SchedulerConfig

    with pytest.raises(ValueError):
        SchedulerConfig(exec_timeout_default_s=200.0, exec_timeout_max_s=100.0)
