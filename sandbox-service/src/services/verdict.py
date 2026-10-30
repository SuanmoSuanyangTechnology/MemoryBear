"""Execution verdict: one terminal status per exec, decided outside the instance.

The backend only reports what happened inside the microVM (stdout, stderr and a raw
error string). Everything the caller relies on is decided here:

* output budget: stdout / stderr are clamped independently, keeping head and tail;
* OOM: judged from an external signal (backend memory event, or a kernel kill while
  the memory peak sits at the limit), never from text the user program printed;
* timeout / cancel / user error / ok: exactly one of them is the terminal status.

Precedence when several signals are present: cancelled > oom > timeout > error > ok.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..backends.base import RunResult

STATUS_OK = "ok"
STATUS_ERROR = "error"  # the user program failed (exception, non-zero exit)
STATUS_TIMEOUT = "timeout"
STATUS_OOM = "oom"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset(
    {STATUS_OK, STATUS_ERROR, STATUS_TIMEOUT, STATUS_OOM, STATUS_CANCELLED}
)

# Raw backend error strings that mean "the OOM killer took the process".
OOM_ERRORS = frozenset({"oom", "oom_killed"})
# Raw backend error strings that mean "the process was killed", cause unknown.
KILL_ERRORS = frozenset({"killed", "kernel_died", "sigkill"})

MEMORY_NEAR_LIMIT = 0.95
ERROR_TEXT_MAX_CHARS = 200


@dataclass(frozen=True)
class OutputBudget:
    stdout_max_bytes: int = 1024 * 1024
    stderr_max_bytes: int = 1024 * 1024
    head_ratio: float = 0.5  # share of the budget kept from the start of the stream

    def __post_init__(self) -> None:
        if self.stdout_max_bytes < 1 or self.stderr_max_bytes < 1:
            raise ValueError("output budgets must be >= 1 byte")
        if not 0.0 <= self.head_ratio <= 1.0:
            raise ValueError("head_ratio must be within [0, 1]")


def clamp_text(text: str, max_bytes: int, head_ratio: float = 0.5) -> tuple[str, int, bool]:
    """Clamp ``text`` to ``max_bytes`` of UTF-8, keeping head and tail.

    Returns ``(text, original_byte_count, truncated)``. When truncated, the kept body
    is at most ``max_bytes`` and a marker with the omitted byte count sits in the
    middle (the marker itself is not counted against the budget). Cuts that land
    inside a multi-byte character drop the partial character.
    """

    raw = text.encode("utf-8", errors="replace")
    total = len(raw)
    if total <= max_bytes:
        return text, total, False
    head_n = int(max_bytes * head_ratio)
    tail_n = max_bytes - head_n
    head = raw[:head_n].decode("utf-8", errors="ignore")
    tail = raw[total - tail_n :].decode("utf-8", errors="ignore") if tail_n else ""
    omitted = total - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n...[truncated {omitted} bytes]...\n{tail}", total, True


@dataclass(frozen=True)
class Verdict:
    status: str
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0  # size before clamping
    stderr_bytes: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def _is_oom(
    raw_error: str | None,
    oom_signal: bool,
    memory_peak_bytes: int | None,
    memory_limit_bytes: int | None,
) -> bool:
    if oom_signal or raw_error in OOM_ERRORS:
        return True
    if raw_error in KILL_ERRORS and memory_peak_bytes and memory_limit_bytes:
        return memory_peak_bytes >= memory_limit_bytes * MEMORY_NEAR_LIMIT
    return False


def judge(
    result: RunResult,
    budget: OutputBudget | None = None,
    *,
    oom_signal: bool = False,
    cancelled: bool = False,
    memory_peak_bytes: int | None = None,
    memory_limit_bytes: int | None = None,
) -> Verdict:
    """Turn a raw ``RunResult`` into the single terminal verdict of one exec."""

    budget = budget or OutputBudget()
    stdout, stdout_bytes, stdout_cut = clamp_text(
        result.stdout, budget.stdout_max_bytes, budget.head_ratio
    )
    stderr, stderr_bytes, stderr_cut = clamp_text(
        result.stderr, budget.stderr_max_bytes, budget.head_ratio
    )

    error: str | None
    if cancelled:
        status, error = STATUS_CANCELLED, STATUS_CANCELLED
    elif _is_oom(result.error, oom_signal, memory_peak_bytes, memory_limit_bytes):
        status, error = STATUS_OOM, STATUS_OOM
    elif result.error == STATUS_TIMEOUT:
        status, error = STATUS_TIMEOUT, STATUS_TIMEOUT
    elif result.error is not None:
        status = STATUS_ERROR
        error = result.error.strip().splitlines()[0][:ERROR_TEXT_MAX_CHARS] if (
            result.error.strip()
        ) else STATUS_ERROR
    else:
        status, error = STATUS_OK, None

    return Verdict(
        status=status,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_truncated=stdout_cut,
        stderr_truncated=stderr_cut,
        error=error,
        duration_ms=result.duration_ms,
    )
