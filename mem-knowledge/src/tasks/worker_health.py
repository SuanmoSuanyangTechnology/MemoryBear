"""Local health state and Kubernetes exec probes for knowledge workers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_PROC_ROOT = Path("/proc")
VALID_PROBES = frozenset({"startup", "live", "ready"})
WORKER_ROLES = frozenset(
    {
        "document_worker",
        "graphrag_worker",
        "qa_import_worker",
    }
)
STATE_FIELDS = frozenset(
    {
        "schema_version",
        "pid",
        "process_start_ticks",
        "role",
        "queues",
        "phase",
        "updated_at_ms",
    }
)
CELERY_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {
        "-A",
        "--app",
        "-b",
        "--broker",
        "--config",
        "--loader",
        "--workdir",
    }
)


class WorkerPhase(StrEnum):
    """Lifecycle phases published by the Celery worker main process."""

    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"


@dataclass(frozen=True)
class WorkerHealthState:
    """Persisted identity and lifecycle state for one worker container."""

    schema_version: int
    pid: int
    process_start_ticks: int
    role: str
    queues: tuple[str, ...]
    phase: WorkerPhase
    updated_at_ms: int


@dataclass(frozen=True)
class ProbeResult:
    """Stable, non-sensitive probe result serialized by the CLI."""

    probe: str
    ok: bool
    reason: str
    role: str | None = None
    pid: int | None = None


class WorkerHealthStateError(ValueError):
    """State parsing failure with a stable public reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_int(value: Any) -> bool:
    return type(value) is int


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _state_payload(state: WorkerHealthState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "pid": state.pid,
        "process_start_ticks": state.process_start_ticks,
        "role": state.role,
        "queues": list(state.queues),
        "phase": state.phase.value,
        "updated_at_ms": state.updated_at_ms,
    }


def read_process_stat(
    pid: int,
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> tuple[str, int]:
    """Return Linux process state and start ticks from /proc."""

    stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise ValueError("invalid process stat")
    fields_after_command = stat[closing_parenthesis + 2 :].split()
    if len(fields_after_command) <= 19:
        raise ValueError("incomplete process stat")
    return fields_after_command[0], int(fields_after_command[19])


def read_process_command(
    pid: int,
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> tuple[str, ...]:
    """Return the NUL-separated command line for one Linux process."""

    raw = (proc_root / str(pid) / "cmdline").read_bytes()
    return tuple(
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    )


def _celery_subcommand(arguments: Sequence[str]) -> str | None:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument in CELERY_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if any(
            argument.startswith(f"{option}=")
            for option in CELERY_GLOBAL_OPTIONS_WITH_VALUE
            if option.startswith("--")
        ):
            index += 1
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument
    return None


def command_is_celery_worker(command: Sequence[str]) -> bool:
    """Recognize supported Celery worker command-line forms."""

    if not command:
        return False
    executable = Path(command[0]).name
    if executable == "celery":
        arguments = command[1:]
    elif executable.startswith("python") and len(command) >= 2:
        if Path(command[1]).name == "celery":
            arguments = command[2:]
        elif len(command) >= 3 and tuple(command[1:3]) == ("-m", "celery"):
            arguments = command[3:]
        else:
            return False
    else:
        return False
    return _celery_subcommand(arguments) == "worker"


def build_worker_state(
    *,
    role: str,
    queues: set[str],
    phase: WorkerPhase,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> WorkerHealthState:
    """Capture the current worker main-process identity."""

    pid = os.getpid()
    process_state, start_ticks = read_process_stat(pid, proc_root=proc_root)
    if process_state == "Z":
        raise ValueError("worker process is a zombie")
    return WorkerHealthState(
        schema_version=SCHEMA_VERSION,
        pid=pid,
        process_start_ticks=start_ticks,
        role=role,
        queues=tuple(sorted(queues)),
        phase=phase,
        updated_at_ms=_now_ms(),
    )


def write_worker_state(path: Path, state: WorkerHealthState) -> None:
    """Atomically persist worker state with owner-only permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(_state_payload(state), temporary_file, separators=(",", ":"))
            temporary_file.flush()
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_worker_state(path: Path) -> WorkerHealthState:
    """Read and strictly validate one persisted worker state."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorkerHealthStateError("state_unavailable") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise WorkerHealthStateError("invalid_state") from error

    if not isinstance(payload, dict) or set(payload) != STATE_FIELDS:
        raise WorkerHealthStateError("invalid_state")

    schema_version = payload["schema_version"]
    if not _is_int(schema_version):
        raise WorkerHealthStateError("invalid_state")
    if schema_version != SCHEMA_VERSION:
        raise WorkerHealthStateError("schema_version_mismatch")

    pid = payload["pid"]
    start_ticks = payload["process_start_ticks"]
    updated_at_ms = payload["updated_at_ms"]
    if (
        not _is_int(pid)
        or pid <= 0
        or not _is_int(start_ticks)
        or start_ticks <= 0
        or not _is_int(updated_at_ms)
        or updated_at_ms < 0
    ):
        raise WorkerHealthStateError("invalid_state")

    role = payload["role"]
    if not isinstance(role, str) or role not in WORKER_ROLES:
        raise WorkerHealthStateError("invalid_state")

    queues = payload["queues"]
    if (
        not isinstance(queues, list)
        or len(queues) != 1
        or any(not isinstance(queue, str) or not queue for queue in queues)
        or len(set(queues)) != len(queues)
    ):
        raise WorkerHealthStateError("queue_mismatch")

    try:
        phase = WorkerPhase(payload["phase"])
    except (TypeError, ValueError) as error:
        raise WorkerHealthStateError("invalid_state") from error

    return WorkerHealthState(
        schema_version=schema_version,
        pid=pid,
        process_start_ticks=start_ticks,
        role=role,
        queues=tuple(queues),
        phase=phase,
        updated_at_ms=updated_at_ms,
    )


def _current_process_owns_state(
    state: WorkerHealthState,
    *,
    proc_root: Path,
) -> bool:
    current_pid = os.getpid()
    if state.pid != current_pid:
        return False
    process_state, start_ticks = read_process_stat(current_pid, proc_root=proc_root)
    return process_state != "Z" and start_ticks == state.process_start_ticks


def transition_worker_state(
    path: Path,
    phase: WorkerPhase,
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> None:
    """Transition state only when it belongs to the current process."""

    state = read_worker_state(path)
    if not _current_process_owns_state(state, proc_root=proc_root):
        raise WorkerHealthStateError("process_identity_mismatch")
    write_worker_state(
        path,
        WorkerHealthState(
            schema_version=state.schema_version,
            pid=state.pid,
            process_start_ticks=state.process_start_ticks,
            role=state.role,
            queues=state.queues,
            phase=phase,
            updated_at_ms=_now_ms(),
        ),
    )


def remove_worker_state_if_owned(
    path: Path,
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> bool:
    """Remove state only when it belongs to the current process."""

    try:
        state = read_worker_state(path)
    except WorkerHealthStateError as error:
        if error.reason == "state_unavailable":
            return False
        raise
    if not _current_process_owns_state(state, proc_root=proc_root):
        return False
    path.unlink(missing_ok=True)
    return True


def _failed_probe(
    probe: str,
    reason: str,
    *,
    state: WorkerHealthState | None = None,
) -> ProbeResult:
    return ProbeResult(
        probe=probe,
        ok=False,
        reason=reason,
        role=state.role if state is not None else None,
        pid=state.pid if state is not None else None,
    )


def evaluate_probe(
    probe: str,
    *,
    state_file: Path,
    expected_role: str,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> ProbeResult:
    """Evaluate one local worker probe without external dependencies."""

    if probe not in VALID_PROBES:
        return _failed_probe(probe, "unknown_probe")
    try:
        state = read_worker_state(state_file)
    except WorkerHealthStateError as error:
        return _failed_probe(probe, error.reason)

    if state.role != expected_role:
        return _failed_probe(probe, "role_mismatch", state=state)

    try:
        os.kill(state.pid, 0)
    except (OSError, OverflowError, ValueError):
        return _failed_probe(probe, "process_not_alive", state=state)

    try:
        first_process_state, first_start_ticks = read_process_stat(
            state.pid,
            proc_root=proc_root,
        )
    except (OSError, UnicodeError, ValueError, IndexError):
        return _failed_probe(probe, "process_not_alive", state=state)
    if first_process_state == "Z":
        return _failed_probe(probe, "process_zombie", state=state)
    if first_start_ticks != state.process_start_ticks:
        return _failed_probe(probe, "process_identity_mismatch", state=state)

    try:
        command = read_process_command(state.pid, proc_root=proc_root)
        final_process_state, final_start_ticks = read_process_stat(
            state.pid,
            proc_root=proc_root,
        )
    except (OSError, UnicodeError, ValueError, IndexError):
        return _failed_probe(probe, "process_not_alive", state=state)
    if final_process_state == "Z":
        return _failed_probe(probe, "process_zombie", state=state)
    if final_start_ticks != state.process_start_ticks or final_start_ticks != first_start_ticks:
        return _failed_probe(probe, "process_identity_mismatch", state=state)
    if not command_is_celery_worker(command):
        return _failed_probe(probe, "not_celery_worker", state=state)

    if probe in {"startup", "ready"} and state.phase is not WorkerPhase.READY:
        return _failed_probe(probe, "worker_not_ready", state=state)
    return ProbeResult(probe=probe, ok=True, reason="ok", role=state.role, pid=state.pid)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one worker probe and print a compact JSON result."""

    from ..bootstrap import get_settings

    arguments = list(sys.argv[1:] if argv is None else argv)
    probe = arguments[0] if len(arguments) == 1 else ""
    settings = get_settings()
    result = evaluate_probe(
        probe,
        state_file=settings.kb_worker_health_state_file,
        expected_role=settings.kb_process_role,
    )
    print(json.dumps(asdict(result), separators=(",", ":")))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ProbeResult",
    "WorkerHealthState",
    "WorkerPhase",
    "build_worker_state",
    "command_is_celery_worker",
    "evaluate_probe",
    "main",
    "read_process_command",
    "read_process_stat",
    "read_worker_state",
    "remove_worker_state_if_owned",
    "transition_worker_state",
    "write_worker_state",
]
