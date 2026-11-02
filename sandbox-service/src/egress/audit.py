"""Egress audit events and sinks.

Every decision, allow or deny, produces one event that carries the four identifiers
the acceptance list (R5) asks for: tenant, workspace, end user and sandbox.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .rules import Decision, EgressRequest


@dataclass(frozen=True)
class EgressContext:
    tenant_id: str
    workspace_id: str
    end_user_id: str
    sandbox_id: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "workspace_id", "end_user_id", "sandbox_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required for an egress audit record")


@dataclass(frozen=True)
class EgressAuditEvent:
    ts_ms: int
    context: EgressContext
    request: EgressRequest
    decision: Decision

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "tenant_id": self.context.tenant_id,
            "workspace_id": self.context.workspace_id,
            "end_user_id": self.context.end_user_id,
            "sandbox_id": self.context.sandbox_id,
            "host": self.request.host,
            "port": self.request.port,
            "protocol": self.request.protocol,
            "resolved_ips": list(self.request.resolved_ips),
            "action": self.decision.action,
            "reason": self.decision.reason,
            "rule": self.decision.rule,
        }


@runtime_checkable
class AuditSink(Protocol):
    def record(self, event: EgressAuditEvent) -> None: ...


class InMemoryAuditSink:
    """Bounded ring buffer; the oldest events are dropped first."""

    def __init__(self, maxlen: int = 10_000) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._events: deque[EgressAuditEvent] = deque(maxlen=maxlen)

    def record(self, event: EgressAuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> list[EgressAuditEvent]:
        return list(self._events)
