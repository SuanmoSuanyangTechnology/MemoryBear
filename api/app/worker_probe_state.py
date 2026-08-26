import json
import os
from pathlib import Path

from celery.signals import worker_init, worker_ready, worker_shutdown


STATE_DIR = Path(os.getenv("CELERY_WORKER_PROBE_DIR", "/tmp/celery-worker-probe"))
STATE_FILE = STATE_DIR / "state.json"
PROC_ROOT = Path("/proc")


def read_process_start_time(pid: int) -> int:
    stat = (PROC_ROOT / str(pid) / "stat").read_text(encoding="utf-8")
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise ValueError("invalid /proc stat format")

    fields_after_command = stat[closing_parenthesis + 2 :].split()
    if len(fields_after_command) <= 19:
        raise ValueError("incomplete /proc stat")
    return int(fields_after_command[19])


def build_state(ready: bool) -> dict[str, int | bool]:
    pid = os.getpid()
    return {
        "pid": pid,
        "start_time": read_process_start_time(pid),
        "ready": ready,
    }


def write_state(ready: bool) -> None:
    state = build_state(ready)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.tmp")
    try:
        temporary_file.write_text(
            json.dumps(state, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_file.replace(STATE_FILE)
    finally:
        temporary_file.unlink(missing_ok=True)


def _state_belongs_to_current_process() -> bool:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return False

        pid = state.get("pid")
        start_time = state.get("start_time")
        current_pid = os.getpid()
        return (
            isinstance(pid, int)
            and not isinstance(pid, bool)
            and pid == current_pid
            and isinstance(start_time, int)
            and not isinstance(start_time, bool)
            and start_time == read_process_start_time(current_pid)
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False


@worker_init.connect
def initialize_probe_state(**kwargs) -> None:
    write_state(ready=False)


@worker_ready.connect
def mark_worker_ready(**kwargs) -> None:
    write_state(ready=True)


@worker_shutdown.connect
def clean_probe_state(**kwargs) -> None:
    if _state_belongs_to_current_process():
        STATE_FILE.unlink(missing_ok=True)
