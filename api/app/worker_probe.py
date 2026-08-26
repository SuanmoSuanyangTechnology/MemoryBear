import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence


STATE_DIR = Path(os.getenv("CELERY_WORKER_PROBE_DIR", "/tmp/celery-worker-probe"))
STATE_FILE = STATE_DIR / "state.json"
PROC_ROOT = Path("/proc")


class StateUnavailableError(Exception):
    pass


class InvalidStateError(Exception):
    pass


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_state() -> dict[str, int | bool]:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StateUnavailableError from error

    if not isinstance(state, dict):
        raise StateUnavailableError

    pid = state.get("pid")
    start_time = state.get("start_time")
    ready = state.get("ready")
    if (
        not _is_int(pid)
        or pid <= 0
        or not _is_int(start_time)
        or start_time <= 0
        or not isinstance(ready, bool)
    ):
        raise InvalidStateError

    return {
        "pid": pid,
        "start_time": start_time,
        "ready": ready,
    }


def read_process_stat(pid: int) -> tuple[str, int]:
    stat = (PROC_ROOT / str(pid) / "stat").read_text(encoding="utf-8")
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise ValueError("invalid /proc stat format")

    fields_after_command = stat[closing_parenthesis + 2 :].split()
    if len(fields_after_command) <= 19:
        raise ValueError("incomplete /proc stat")
    return fields_after_command[0], int(fields_after_command[19])


def read_command(pid: int) -> list[str]:
    raw = (PROC_ROOT / str(pid) / "cmdline").read_bytes()
    return [
        argument.decode("utf-8", errors="replace")
        for argument in raw.split(b"\0")
        if argument
    ]


CELERY_GLOBAL_OPTIONS_WITH_VALUE = {
    "-A",
    "--app",
    "-b",
    "--broker",
    "--config",
    "--loader",
    "--workdir",
}


def _celery_subcommand(arguments: Sequence[str]) -> str | None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument in CELERY_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument
    return None


def command_is_celery_worker(command: Sequence[str]) -> tuple[bool, str]:
    if not command:
        return False, "not_celery_process"

    executable = Path(command[0]).name
    if executable == "celery":
        arguments = list(command[1:])
    elif executable.startswith("python") and len(command) >= 2:
        if Path(command[1]).name == "celery":
            arguments = list(command[2:])
        elif len(command) >= 3 and command[1:3] == ["-m", "celery"]:
            arguments = list(command[3:])
        else:
            return False, "not_celery_process"
    else:
        return False, "not_celery_process"

    subcommand = _celery_subcommand(arguments)
    if subcommand != "worker":
        return False, "not_worker_command"
    return True, "ok"


def validate_process(state: dict[str, int | bool]) -> tuple[bool, str]:
    pid = int(state["pid"])
    expected_start_time = int(state["start_time"])

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False, "process_not_alive"
    except (OSError, OverflowError, ValueError):
        return False, "invalid_state"

    try:
        process_state, actual_start_time = read_process_stat(pid)
    except (OSError, UnicodeError, ValueError, IndexError):
        return False, "process_not_alive"

    if process_state == "Z":
        return False, "process_zombie"
    if actual_start_time != expected_start_time:
        return False, "process_identity_mismatch"

    try:
        command = read_command(pid)
        final_process_state, final_start_time = read_process_stat(pid)
    except (OSError, UnicodeError, ValueError, IndexError):
        return False, "process_not_alive"

    if final_process_state == "Z":
        return False, "process_zombie"
    if final_start_time != expected_start_time:
        return False, "process_identity_mismatch"
    return command_is_celery_worker(command)


def run_probe(probe: str) -> tuple[bool, str, int | None]:
    if probe not in {"startup", "live"}:
        return False, "unknown_probe", None

    try:
        state = read_state()
    except StateUnavailableError:
        return False, "state_unavailable", None
    except InvalidStateError:
        return False, "invalid_state", None

    pid = int(state["pid"])
    process_ok, reason = validate_process(state)
    if not process_ok:
        return False, reason, pid

    if probe == "startup" and state["ready"] is not True:
        return False, "worker_not_ready", pid
    return True, "ok", pid


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    probe = arguments[0] if len(arguments) == 1 else ""

    ok, reason, pid = run_probe(probe)
    print(json.dumps({"probe": probe, "ok": ok, "reason": reason, "pid": pid}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
