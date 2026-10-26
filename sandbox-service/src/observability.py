"""Low-cardinality Prometheus metrics and durable JSONL operation audit.

Metrics are rendered without a client-library dependency because this service only needs
one process-local scrape endpoint. Audit records deliberately keep request identity out of
metric labels while retaining it in the append-only audit file for incident investigation.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditConfig:
    audit_log_path: str = "logs/sandbox-audit.jsonl"

    def __post_init__(self) -> None:
        if not self.audit_log_path.strip():
            raise ValueError("audit_log_path must not be empty")


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
    """Append one compact JSON object per line and flush every accepted event."""

    def __init__(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
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

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._file.close()


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
        ]
    )
    return "\n".join(lines) + "\n"
