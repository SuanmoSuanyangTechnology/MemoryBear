"""Terminal command helpers: validation, wrapper payload, result folding."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from src.backends import RunResult
from src.errors import SandboxError
from src.services.shell import (
    ERROR_WRAPPER_FAILED,
    MAX_COMMAND_BYTES,
    MAX_ENV_ITEMS,
    build_wrapper,
    fold_result,
    parse_output,
    resolve_cwd,
    validate_command,
    validate_env,
)


def emit(marker: str, **over) -> str:
    data = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "error": None}
    data.update(over)
    return f"noise\n{marker}{json.dumps(data)}\n"


# -- validation ----------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, 1, "", "   ", "a\x00b"])
def test_bad_commands_are_rejected(bad):
    with pytest.raises(SandboxError) as ei:
        validate_command(bad)
    assert ei.value.kind == "invalid_request"


def test_oversize_command_is_rejected():
    # Built inside the test: a 64KB parametrize id would overflow PYTEST_CURRENT_TEST on Windows.
    with pytest.raises(SandboxError) as ei:
        validate_command("x" * (MAX_COMMAND_BYTES + 1))
    assert ei.value.kind == "invalid_request"
    assert validate_command("x" * MAX_COMMAND_BYTES)


def test_good_command_passes():
    assert validate_command("ls -la") == "ls -la"


def test_cwd_defaults_to_the_conversation_directory():
    assert resolve_cwd(None, "c1") == "/workspace/sessions/c1"
    assert resolve_cwd("  ", "c1") == "/workspace/sessions/c1"


def test_cwd_is_normalised_and_kept_inside_workspace():
    assert resolve_cwd("/workspace/a/../b", "c") == "/workspace/b"
    assert resolve_cwd("/workspace", "c") == "/workspace"
    assert resolve_cwd("/workspace/", "c") == "/workspace"


@pytest.mark.parametrize(
    "bad", ["/etc", "/workspace/../etc", "/workspacex", "relative/dir", "/workspace/a\x00"]
)
def test_cwd_outside_workspace_is_rejected(bad):
    with pytest.raises(SandboxError) as ei:
        resolve_cwd(bad, "c")
    assert ei.value.kind == "invalid_request"


def test_env_validation():
    assert validate_env(None) == {}
    assert validate_env({"A_1": "x"}) == {"A_1": "x"}
    for bad in (
        {"1A": "x"},
        {"A-B": "x"},
        {"A": "x\x00"},
        {"A": "x" * 5000},
        {f"K{i}": "v" for i in range(MAX_ENV_ITEMS + 1)},
    ):
        with pytest.raises(SandboxError):
            validate_env(bad)


# -- wrapper -------------------------------------------------------------------------


def test_wrapper_is_valid_python_and_marker_differs_per_call():
    code, marker = build_wrapper("echo 'hi' \"there\" \\ $(x)", "/workspace", {"A": "b"}, 30)
    compile(code, "<wrapper>", "exec")
    assert marker in code
    _, marker2 = build_wrapper("echo", "/workspace", {}, 30)
    assert marker != marker2


def test_command_text_cannot_break_out_of_the_payload():
    nasty = "'''\n__import__('os').system('boom')\n'''"
    code, _ = build_wrapper(nasty, "/workspace", {}, 30)
    compile(code, "<wrapper>", "exec")
    assert "\n__import__('os')" not in code  # only present inside the escaped literal


# -- parse / fold --------------------------------------------------------------------


def test_parse_output_reads_the_last_marker_only():
    out = parse_output(emit("@@M@@", stdout="a") + emit("@@M@@", stdout="b"), "@@M@@")
    assert out is not None and out.stdout == "b"


def test_parse_output_rejects_missing_or_broken_payload():
    assert parse_output("nothing here", "@@M@@") is None
    assert parse_output("@@M@@not json", "@@M@@") is None
    assert parse_output("@@M@@[1, 2]", "@@M@@") is None


def test_fold_success_has_zero_exit_and_no_error():
    res, code = fold_result(RunResult(stdout=emit("@@M@@", stdout="hi\n")), "@@M@@")
    assert code == 0 and res.error is None and res.stdout == "hi\n"


def test_fold_non_zero_exit_becomes_an_error():
    raw = RunResult(stdout=emit("@@M@@", exit_code=2, stderr="bad"), duration_ms=7)
    res, code = fold_result(raw, "@@M@@")
    assert code == 2 and res.error == "exit code 2" and res.stderr == "bad"
    assert res.duration_ms == 7


def test_fold_timeout_wins_over_exit_code():
    raw = RunResult(stdout=emit("@@M@@", exit_code=None, timed_out=True))
    res, code = fold_result(raw, "@@M@@")
    assert code is None and res.error == "timeout"


def test_fold_start_failure_is_reported():
    raw = RunResult(stdout=emit("@@M@@", exit_code=None, error="cannot start command: OSError"))
    res, code = fold_result(raw, "@@M@@")
    assert code is None and res.error == "cannot start command: OSError"


def test_fold_without_marker_keeps_backend_error_or_flags_wrapper_failure():
    res, code = fold_result(RunResult(error="timeout", stderr="e"), "@@M@@")
    assert code is None and res.error == "timeout" and res.stderr == "e"
    res, _ = fold_result(RunResult(stdout="garbage"), "@@M@@")
    assert res.error == ERROR_WRAPPER_FAILED


def test_command_output_cannot_spoof_the_result():
    # The command prints a fake marker line, but it travels inside the JSON string.
    code, marker = build_wrapper("echo hi", "/workspace", {}, 30)
    assert marker not in "@@SBX-fake@@" and parse_output("@@SBX-fake@@{}", marker) is None


# -- real run of the wrapper (POSIX only) --------------------------------------------


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs /bin/bash and POSIX signals")


def run_wrapper(cmd: str, cwd, timeout_s: float, env=None):
    code, marker = build_wrapper(cmd, str(cwd), env or {}, timeout_s)
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return fold_result(RunResult(stdout=proc.stdout), marker)


@posix_only
def test_real_run_captures_streams_and_exit_code(tmp_path):
    res, code = run_wrapper("echo out; echo err >&2; exit 3", tmp_path, 30)
    assert code == 3
    assert res.stdout == "out\n" and res.stderr == "err\n"
    assert res.error == "exit code 3"


@posix_only
def test_real_run_uses_cwd_and_env(tmp_path):
    res, code = run_wrapper("pwd; echo $SBX_TEST_VAR", tmp_path, 30, {"SBX_TEST_VAR": "v1"})
    assert code == 0
    lines = res.stdout.split()
    assert lines[0].endswith(tmp_path.name) and lines[1] == "v1"


@posix_only
def test_real_run_creates_a_missing_cwd(tmp_path):
    target = tmp_path / "new" / "dir"
    _res, code = run_wrapper("true", target, 30)
    assert code == 0 and target.is_dir()


@posix_only
def test_real_run_kills_the_whole_process_group_on_timeout(tmp_path):
    marker_file = tmp_path / "survivor"
    # The background child would create the file after 3s if it survived the kill.
    cmd = f"(sleep 3; touch {marker_file}) & sleep 30"
    res, code = run_wrapper(cmd, tmp_path, 2)
    assert code is None and res.error == "timeout"
    import time

    time.sleep(4)
    assert not marker_file.exists()


@posix_only
def test_real_run_background_process_does_not_hold_the_call(tmp_path):
    import time

    survivor = tmp_path / "alive"
    # No redirection on purpose: with pipes this would block until the timeout.
    cmd = f"(sleep 2; touch {survivor}) & echo started"
    started = time.monotonic()
    res, code = run_wrapper(cmd, tmp_path, 20)
    assert time.monotonic() - started < 1.5
    assert code == 0 and res.stdout == "started\n" and res.error is None
    time.sleep(3)
    assert survivor.exists()  # the background process outlived the call


@posix_only
def test_real_run_truncates_huge_output_with_a_marker(tmp_path):
    res, code = run_wrapper("head -c 5000000 /dev/zero | tr '\\0' x", tmp_path, 30)
    assert code == 0
    assert "[truncated" in res.stdout and len(res.stdout) < 4_500_000


@posix_only
def test_real_run_has_no_stdin(tmp_path):
    res, code = run_wrapper("cat", tmp_path, 30)
    assert code == 0 and res.stdout == ""


# -- reaper -----------------------------------------------------------------------------


def test_reaper_is_valid_python_and_targets_the_conversation_file():
    from src.services.shell import build_reaper, pgfile_for

    code = build_reaper("conv-1")
    compile(code, "<reaper>", "exec")
    assert pgfile_for("conv-1") in code
    assert pgfile_for("conv-1") != pgfile_for("conv-2")


def test_wrapper_records_process_groups_only_when_given_a_conversation():
    code, _ = build_wrapper("true", "/workspace", {}, 10, "conv-1")
    from src.services.shell import pgfile_for

    assert pgfile_for("conv-1") in code
    anonymous, _ = build_wrapper("true", "/workspace", {}, 10)
    assert pgfile_for("conv-1") not in anonymous


@posix_only
def test_real_reaper_kills_background_groups_of_the_conversation_only(tmp_path):
    import os
    import time
    import uuid

    from src.services.shell import build_reaper, pgfile_for

    conv_a, conv_b = f"a-{uuid.uuid4()}", f"b-{uuid.uuid4()}"
    mine, other = tmp_path / "mine", tmp_path / "other"

    def start(conv, target):
        code, marker = build_wrapper(
            f"(sleep 3; touch {target}) & echo up", str(tmp_path), {}, 20, conv
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        res, exit_code = fold_result(RunResult(stdout=proc.stdout), marker)
        assert exit_code == 0 and res.stdout == "up\n"

    try:
        start(conv_a, mine)
        start(conv_b, other)
        reaper = subprocess.run(
            [sys.executable, "-c", build_reaper(conv_a)], capture_output=True, text=True, timeout=60
        )
        assert reaper.returncode == 0, reaper.stderr
        assert '"killed": 1' in reaper.stdout
        assert not os.path.exists(pgfile_for(conv_a))
        time.sleep(4.5)
        assert not mine.exists()  # reaped before it could touch the file
        assert other.exists()  # the other conversation's background process lived on
    finally:
        for conv in (conv_a, conv_b):
            try:
                os.unlink(pgfile_for(conv))
            except OSError:
                pass


def test_reaper_without_a_record_is_a_noop():
    import sys as _sys

    from src.services.shell import build_reaper

    if _sys.platform == "win32":
        pytest.skip("reaper reads /proc")
    proc = subprocess.run(
        [_sys.executable, "-c", build_reaper("never-ran-" + "x" * 8)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0 and '"killed": 0' in proc.stdout
