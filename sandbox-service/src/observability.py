"""Low-cardinality Prometheus metrics and durable JSONL operation audit.

Metrics are rendered without a client-library dependency because this service only needs
one process-local scrape endpoint. Audit records deliberately keep request identity out of
metric labels while retaining it in the audit stream (stdout by default, so the cluster log
pipeline owns retention) for incident investigation.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO, runtime_checkable

logger = logging.getLogger(__name__)


AUDIT_SINK_MODES = ("stdout", "file", "none")


@dataclass(frozen=True)
class AuditConfig:
    """Where operation audit records go.

    ``stdout`` (default) writes one JSON object per line to the process standard
    output, so the cluster log pipeline owns retention, search and rotation - the same
    way the other services are deployed, and no volume has to be mounted. ``file``
    appends to ``audit_log_path`` and rotates by size, for hosts without log
    collection. ``none`` disables audit as an explicit opt-out.
    """

    audit_sink: str = "stdout"
    audit_log_path: str = "logs/sandbox-audit.jsonl"
    # Bounded queue between the request path and the writer; a full queue drops and counts.
    audit_queue_size: int = 10000
    # File mode only: rotate once the current file passes this size, keep this many backups.
    audit_max_bytes: int = 64 * 1024 * 1024
    audit_backup_count: int = 10

    def __post_init__(self) -> None:
        mode = self.audit_sink.strip().lower()
        if mode not in AUDIT_SINK_MODES:
            raise ValueError(f"audit_sink must be one of {', '.join(AUDIT_SINK_MODES)}")
        object.__setattr__(self, "audit_sink", mode)
        if not self.audit_log_path.strip():
            raise ValueError("audit_log_path must not be empty")
        if self.audit_queue_size < 1:
            raise ValueError("audit_queue_size must be >= 1")
        if self.audit_max_bytes < 1:
            raise ValueError("audit_max_bytes must be >= 1")
        if self.audit_backup_count < 0:
            raise ValueError("audit_backup_count must be >= 0")


@dataclass(frozen=True)
class OperationAuditEvent:
    ts_ms: int
    operation: str
    outcome: str
    tenant_id: str | None = None
    workspace_id: str | None = None
    end_user_id: str | None = None
    sandbox_digest: str | None = None
    instance_id: str | None = None
    conversation_id: str | None = None
    channel: str | None = None
    reason: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@runtime_checkable
class OperationAuditSink(Protocol):
    def record(self, event: OperationAuditEvent) -> None: ...

    def close(self) -> None: ...


class NullOperationAuditSink:
    def record(self, event: OperationAuditEvent) -> None:
        del event

    def close(self) -> None:
        pass


class InMemoryOperationAuditSink:
    """Small test/diagnostic sink; production uses the append-only JSONL sink."""

    def __init__(self) -> None:
        self.events: list[OperationAuditEvent] = []
        self.closed = False

    def record(self, event: OperationAuditEvent) -> None:
        if self.closed:
            raise RuntimeError("operation audit sink is closed")
        self.events.append(event)

    def close(self) -> None:
        self.closed = True


class JsonlOperationAuditSink:
    """Append one compact JSON object per line, flushing every accepted event.

    Rotates once the file passes ``max_bytes``, keeping ``backup_count`` older files, so a
    file-mode deployment does not need an external logrotate.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        backup_count: int = 10,
    ) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._file = target.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._closed = False

    def record(self, event: OperationAuditEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if self._closed:
                raise RuntimeError("operation audit sink is closed")
            self._file.write(line + "\n")
            self._file.flush()
            if self.max_bytes > 0 and self._file.tell() >= self.max_bytes:
                self._rotate_locked()

    def _rotate_locked(self) -> None:
        self._file.close()
        target = self.path
        if self.backup_count > 0:
            oldest = target.with_name(f"{target.name}.{self.backup_count}")
            if oldest.exists():
                oldest.unlink()
            for index in range(self.backup_count - 1, 0, -1):
                source = target.with_name(f"{target.name}.{index}")
                if source.exists():
                    source.replace(target.with_name(f"{target.name}.{index + 1}"))
            if target.exists():
                target.replace(target.with_name(f"{target.name}.1"))
        else:
            target.unlink(missing_ok=True)
        self._file = target.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._file.close()


class StdoutOperationAuditSink:
    """Write one JSON object per line to a text stream (stdout by default).

    Used when the cluster collects pod stdout: retention, search, rotation and access
    control are owned by the log pipeline instead of a mounted volume.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()
        self._closed = False

    def record(self, event: OperationAuditEvent) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if self._closed:
                raise RuntimeError("operation audit sink is closed")
            self._stream.write(line + "\n")
            self._stream.flush()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._stream.flush()
                except ValueError:  # pragma: no cover - stream already closed by the runtime
                    pass


class AsyncOperationAuditSink:
    """Move audit writes off the request path.

    ``record`` only appends to a bounded queue; a daemon thread performs the actual write, so
    a slow volume or a stalled log collector never adds latency to a tool call. When the
    queue is full the event is dropped and counted (``dropped``); a failed write is counted
    (``failures``) and never propagates to the caller.
    """

    def __init__(self, inner: OperationAuditSink, *, queue_size: int = 10000) -> None:
        self._inner = inner
        self._queue: queue.Queue[OperationAuditEvent | None] = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self.dropped = 0
        self.failures = 0
        self._closed = False
        self._worker = threading.Thread(target=self._drain, name="sandbox-audit", daemon=True)
        self._worker.start()

    def record(self, event: OperationAuditEvent) -> None:
        with self._lock:
            if self._closed:
                return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self.dropped += 1

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                try:
                    self._inner.record(item)
                except Exception:  # noqa: BLE001 - the writer thread must never crash
                    with self._lock:
                        self.failures += 1
                    logger.exception("operation audit write failed: operation=%s", item.operation)
            finally:
                self._queue.task_done()

    def close(self) -> None:
        """Drain what is already queued, then close the wrapped sink."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=5.0)
        self._inner.close()

    def counters(self) -> dict[str, int]:
        with self._lock:
            return {"dropped": self.dropped, "failures": self.failures}


def build_audit_sink(config: AuditConfig) -> OperationAuditSink:
    """Create the sink selected by ``SANDBOX_AUDIT_SINK``, always behind the async queue."""

    if config.audit_sink == "none":
        return NullOperationAuditSink()
    if config.audit_sink == "file":
        inner: OperationAuditSink = JsonlOperationAuditSink(
            config.audit_log_path,
            max_bytes=config.audit_max_bytes,
            backup_count=config.audit_backup_count,
        )
    else:
        inner = StdoutOperationAuditSink()
    return AsyncOperationAuditSink(inner, queue_size=config.audit_queue_size)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: int | float, labels: dict[str, str] | None = None) -> str:
    suffix = ""
    if labels:
        rendered = ",".join(
            f'{key}="{_escape_label(label)}"' for key, label in sorted(labels.items())
        )
        suffix = "{" + rendered + "}"
    return f"{name}{suffix} {value}"


def render_prometheus(manager: Any) -> str:
    """Render manager state and bounded internal counters in Prometheus text format."""

    stats = manager.stats()
    counters = stats.get("metrics", {})
    states = stats.get("by_state", {})
    lines = [
        "# HELP sandbox_instances Current sandbox instances by state.",
        "# TYPE sandbox_instances gauge",
    ]
    for state in ("creating", "running", "paused"):
        lines.append(_sample("sandbox_instances", int(states.get(state, 0)), {"state": state}))
    lines.extend(
        [
            "# HELP sandbox_queue_depth Requests waiting for sandbox capacity.",
            "# TYPE sandbox_queue_depth gauge",
            _sample("sandbox_queue_depth", int(stats.get("queue_depth", 0))),
            "# HELP sandbox_operations_total Completed service operations.",
            "# TYPE sandbox_operations_total counter",
        ]
    )

    for key, value in sorted(counters.items()):
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "operation":
            lines.append(
                _sample(
                    "sandbox_operations_total",
                    int(value),
                    {"operation": parts[1], "outcome": parts[2]},
                )
            )

    lines.extend(
        [
            "# HELP sandbox_backend_errors_total Backend failures by operation and kind.",
            "# TYPE sandbox_backend_errors_total counter",
        ]
    )
    for key, value in sorted(counters.items()):
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "backend_error":
            lines.append(
                _sample(
                    "sandbox_backend_errors_total",
                    int(value),
                    {"operation": parts[1], "kind": parts[2]},
                )
            )

    lines.extend(
        [
            "# HELP sandbox_lifecycle_total Sandbox lifecycle transitions.",
            "# TYPE sandbox_lifecycle_total counter",
        ]
    )
    for key, value in sorted(counters.items()):
        parts = key.split(".")
        if len(parts) == 2 and parts[0] == "lifecycle":
            lines.append(_sample("sandbox_lifecycle_total", int(value), {"action": parts[1]}))

    lines.extend(
        [
            "# HELP sandbox_execution_paths_total Execution acquisition paths.",
            "# TYPE sandbox_execution_paths_total counter",
        ]
    )
    for path in ("create", "resume", "hit"):
        lines.append(
            _sample(
                "sandbox_execution_paths_total",
                int(counters.get(f"path.{path}", 0)),
                {"path": path},
            )
        )
    lines.extend(
        [
            (
                "# HELP sandbox_egress_policy_applied_total "
                "Sandboxes created with strict egress policy."
            ),
            "# TYPE sandbox_egress_policy_applied_total counter",
            _sample(
                "sandbox_egress_policy_applied_total",
                int(counters.get("egress_policy.applied", 0)),
            ),
            "# HELP sandbox_audit_write_failures_total Audit records that could not be persisted.",
            "# TYPE sandbox_audit_write_failures_total counter",
            _sample(
                "sandbox_audit_write_failures_total",
                int(counters.get("audit_write_failures", 0)),
            ),
            "# HELP sandbox_audit_dropped_total Audit records dropped because the queue was full.",
            "# TYPE sandbox_audit_dropped_total counter",
            _sample(
                "sandbox_audit_dropped_total",
                int(counters.get("audit_dropped", 0)),
            ),
        ]
    )
    return "\n".join(lines) + "\n"
