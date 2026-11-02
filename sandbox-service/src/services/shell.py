"""Terminal commands, run through a dedicated python kernel of the conversation.

Why not call the substrate's process API: it needs a new transport (Connect RPC
streaming) and real-machine checks. A kernel already exists, is already scheduled,
timed out, budgeted and reported by the manager, so a command is just a small
python wrapper that launches ``/bin/bash -lc <cmd>`` and prints one JSON line.

The wrapper, not the user command, writes to the kernel stdout. The command's own
stdout / stderr travel inside the JSON, so they can neither be mistaken for the
result nor spoof it; the per-call marker only guards against stray kernel output.

Semantics (same as design doc 6.4): one fresh process per call, no shell state kept,
no stdin, no PTY. On timeout the whole process group is killed.

Background processes: the command's output goes to temp files and the call returns
as soon as bash itself exits. A process started with ``&`` (a dev server, a worker)
therefore does not hold the call and keeps running until the instance is recycled.
Only on timeout is the whole process group killed, background children included.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import secrets
from dataclasses import dataclass

from ..backends.base import RunResult
from ..errors import invalid_request

WORKSPACE_ROOT = "/workspace"
MAX_COMMAND_BYTES = 64 * 1024
MAX_ENV_ITEMS = 16
MAX_ENV_VALUE_CHARS = 4096
# Bound for what the wrapper captures per stream before the manager's own budget applies.
CAPTURE_MAX_BYTES = 4 * 1024 * 1024
KILL_WAIT_S = 5

ERROR_WRAPPER_FAILED = "shell_wrapper_failed"

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_WRAPPER = '''\
def _sbx_main(p):
    import json, os, signal, subprocess, tempfile

    cap = p["cap"]

    def cut(f):
        size = f.seek(0, 2)
        f.seek(0)
        if size <= cap:
            return f.read().decode("utf-8", "replace")
        half = cap // 2
        head = f.read(half)
        f.seek(size - half)
        tail = f.read(half)
        return (
            head.decode("utf-8", "replace")
            + "\\n...[truncated %d bytes]...\\n" % (size - 2 * half)
            + tail.decode("utf-8", "replace")
        )

    res = {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False, "error": None}
    env = dict(os.environ)
    env.update(p["env"])
    # Output goes to unlinked temp files, not pipes: we wait for bash itself, so a
    # background process (``server &``) that keeps the descriptors open cannot hold
    # the call until the timeout, and it keeps running after the call returns.
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = None
        try:
            os.makedirs(p["cwd"], exist_ok=True)
            proc = subprocess.Popen(
                ["/bin/bash", "-lc", p["cmd"]],
                cwd=p["cwd"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                start_new_session=True,
            )
        except OSError as exc:
            res["error"] = "cannot start command: " + type(exc).__name__
        if proc is not None:
            if p.get("pgfile"):
                # Remember the process group (== pid, the command is a session leader)
                # so the conversation's leftovers can be killed when its kernel is retired.
                try:
                    with open(p["pgfile"], "a") as pf:
                        pf.write("%d\\n" % proc.pid)
                except OSError:
                    pass
            try:
                proc.wait(timeout=p["timeout"])
                res["exit_code"] = proc.returncode
            except subprocess.TimeoutExpired:
                res["timed_out"] = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=p["kill_wait"])
                except subprocess.TimeoutExpired:
                    pass
        res["stdout"] = cut(out_f)
        res["stderr"] = cut(err_f)
    print(p["marker"] + json.dumps(res))


_sbx_main(json.loads(__PAYLOAD__))
'''


# Kills the process groups recorded by the wrapper that are still alive. A group is only
# touched when some live process is its session leader group (pgrp == session == recorded
# pid), which guards against pid reuse. Processes that left the group (setsid, double
# fork) are out of reach by design.
_REAPER = '''\
def _sbx_reap(p):
    import os, signal

    try:
        with open(p["pgfile"]) as f:
            groups = {int(t) for t in f.read().split() if t.isdigit()}
    except OSError:
        groups = set()
    own = {os.getpgrp(), os.getsid(0)}
    live = set()
    try:
        names = os.listdir("/proc")
    except OSError:
        names = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/stat") as f:
                rest = f.read().rsplit(")", 1)[1].split()
            pgrp, sess = int(rest[2]), int(rest[3])
        except (OSError, IndexError, ValueError):
            continue
        if pgrp == sess and pgrp in groups:
            live.add(pgrp)
    killed = 0
    for pg in sorted(live - own):
        if pg <= 1:
            continue
        try:
            os.killpg(pg, signal.SIGKILL)
            killed += 1
        except OSError:
            pass
    try:
        os.unlink(p["pgfile"])
    except OSError:
        pass
    print(p["marker"] + json.dumps({"killed": killed}))


_sbx_reap(json.loads(__PAYLOAD__))
'''


def pgfile_for(conversation_id: str) -> str:
    """Per-conversation file listing the process groups the terminal started."""

    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
    return f"/tmp/.sbx-pg-{digest}"


@dataclass(frozen=True)
class ShellOutput:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None


# -- request validation ---------------------------------------------------------------


def validate_command(cmd: object) -> str:
    if not isinstance(cmd, str) or not cmd.strip():
        raise invalid_request("cmd is required", field="cmd")
    if "\x00" in cmd:
        raise invalid_request("cmd contains a NUL byte", field="cmd")
    if len(cmd.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise invalid_request(f"cmd is too long (max {MAX_COMMAND_BYTES} bytes)", field="cmd")
    return cmd


def resolve_cwd(cwd: str | None, conversation_id: str) -> str:
    """Default is the conversation directory; anything else must stay under /workspace."""

    raw = cwd if cwd is not None and cwd.strip() else f"{WORKSPACE_ROOT}/sessions/{conversation_id}"
    if "\x00" in raw:
        raise invalid_request("cwd contains a NUL byte", field="cwd")
    if not raw.startswith("/"):
        raise invalid_request("cwd must be an absolute path", field="cwd")
    path = posixpath.normpath(raw)
    if path != WORKSPACE_ROOT and not path.startswith(WORKSPACE_ROOT + "/"):
        raise invalid_request(f"cwd must be inside {WORKSPACE_ROOT}", field="cwd")
    return path


def build_chdir_code(conversation_id: str) -> str:
    """Python snippet that moves a code kernel into its conversation directory.

    Relative paths written by the code interpreter then land in
    ``/workspace/sessions/<conversation_id>`` instead of the per-user shared root.
    The helper is deleted afterwards so no name leaks into the user's namespace.
    """

    target = resolve_cwd(None, conversation_id)
    return (
        "def _sbx_cd(d):\n"
        "    import os\n"
        "    os.makedirs(d, exist_ok=True)\n"
        "    os.chdir(d)\n"
        f"_sbx_cd({target!r})\n"
        "del _sbx_cd\n"
    )


def validate_env(env: dict[str, str] | None) -> dict[str, str]:
    if not env:
        return {}
    if len(env) > MAX_ENV_ITEMS:
        raise invalid_request(f"env has too many entries (max {MAX_ENV_ITEMS})", field="env")
    for name, value in env.items():
        if not isinstance(name, str) or not _ENV_NAME.match(name):
            raise invalid_request("env contains an invalid variable name", field="env")
        if not isinstance(value, str) or len(value) > MAX_ENV_VALUE_CHARS or "\x00" in value:
            raise invalid_request(f"env value of {name} is invalid", field="env")
    return dict(env)


# -- wrapper / result ------------------------------------------------------------------


def build_reaper(conversation_id: str) -> str:
    """Python code that kills the conversation's leftover terminal process groups."""

    payload = {"pgfile": pgfile_for(conversation_id), "marker": f"@@SBX-{secrets.token_hex(8)}@@"}
    return "import json\n" + _REAPER.replace("__PAYLOAD__", repr(json.dumps(payload)))


def build_wrapper(
    cmd: str,
    cwd: str,
    env: dict[str, str],
    timeout_s: float,
    conversation_id: str | None = None,
) -> tuple[str, str]:
    """Return ``(python code, marker)``; the marker prefixes the JSON result line."""

    marker = f"@@SBX-{secrets.token_hex(8)}@@"
    # The wrapper stops its own process a little before the manager's deadline so the
    # normal path is a clean timed_out report, not a kernel restart.
    inner_timeout = max(timeout_s - 1.0, timeout_s * 0.5)
    payload = {
        "cmd": cmd,
        "cwd": cwd,
        "env": env,
        "timeout": inner_timeout,
        "kill_wait": KILL_WAIT_S,
        "cap": CAPTURE_MAX_BYTES,
        "marker": marker,
        "pgfile": pgfile_for(conversation_id) if conversation_id else None,
    }
    # ``repr`` of a str is a valid python literal, so the payload cannot break out.
    code = _WRAPPER.replace("__PAYLOAD__", repr(json.dumps(payload)))
    code = "import json\n" + code
    return code, marker


def parse_output(text: str, marker: str) -> ShellOutput | None:
    idx = text.rfind(marker)
    if idx < 0:
        return None
    try:
        data = json.loads(text[idx + len(marker) :].strip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    exit_code = data.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = None
    error = data.get("error")
    return ShellOutput(
        exit_code=exit_code,
        stdout=str(data.get("stdout") or ""),
        stderr=str(data.get("stderr") or ""),
        timed_out=bool(data.get("timed_out")),
        error=str(error) if error else None,
    )


def fold_result(raw: RunResult, marker: str) -> tuple[RunResult, int | None]:
    """Turn the wrapper's raw kernel result into the command's result and exit code.

    A non-zero exit code is reported as ``error`` so the verdict marks it ``error``,
    exactly like a user program that raised.
    """

    parsed = parse_output(raw.stdout, marker)
    if parsed is None:
        # Timeout / kernel death decided outside, or the wrapper itself failed.
        return (
            RunResult(
                stderr=raw.stderr,
                error=raw.error or ERROR_WRAPPER_FAILED,
                duration_ms=raw.duration_ms,
            ),
            None,
        )
    error: str | None
    if parsed.timed_out:
        error = "timeout"
    elif parsed.error:
        error = parsed.error
    elif parsed.exit_code not in (None, 0):
        error = f"exit code {parsed.exit_code}"
    else:
        error = None
    return (
        RunResult(
            stdout=parsed.stdout,
            stderr=parsed.stderr,
            error=error,
            duration_ms=raw.duration_ms,
        ),
        parsed.exit_code,
    )
