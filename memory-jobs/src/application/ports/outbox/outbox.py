"""Ports of the storage-outbox projection worker.

The API keeps the write-side of the outbox: every Neo4j memory-node mutation
enqueues a row into ``memory_storage_outbox_events`` in the same business
database. This service owns the read-side: it leases pending rows, re-reads the
authoritative node from Neo4j and projects it into Elasticsearch.

Two ports keep the application layer free of drivers:

``OutboxStore``
    Leases and terminal state of the PostgreSQL outbox rows. The contract is
    deliberately shaped after the lease semantics the API already implements
    (``FOR UPDATE SKIP LOCKED`` claim, heartbeat renewal, in-place attempts)
    because the two services may briefly run at the same time: the PostgreSQL
    row, not Redis, is the single source of truth for "who projects this node".

``OutboxProjector``
    Re-reads one authoritative node and writes/removes its Elasticsearch
    document. ``check_claim`` is the lease probe handed to the projector: it
    raises :class:`OutboxClaimLost` as soon as this worker no longer owns the
    row, so a slow Neo4j/ES round-trip cannot overwrite a document another
    worker took over.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

#: Maximum attempts of one event, counting the first try. The API hardcodes the
#: same value in its CHECK constraint (``attempt_count BETWEEN 0 AND 3``); the
#: worker may never spend more attempts than the column can store.
MAX_ATTEMPTS = 3


class OutboxClaimLost(Exception):
    """Raised when the lease on an event no longer belongs to this worker."""


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """One leased row of ``memory_storage_outbox_events``."""

    id: UUID
    sequence: int
    label: str
    node_id: str
    operation: str
    attempt_count: int
    claim_token: UUID
    #: Highest sequence pending for this node at claim time. The projection
    #: re-reads Neo4j, so every row already queued then is covered by the state
    #: just written and may converge to a terminal state without projecting.
    #: ``0`` disables the collapse (acknowledge this row only).
    queue_tip: int = 0


@dataclass(slots=True)
class ProjectionReport:
    """Outcome counters of one batch (the API task's return value shape)."""

    claimed: int = 0
    processed: int = 0
    failed: int = 0
    lost: int = 0
    expired: int = 0

    def add(self, outcome: str) -> None:
        """Count one event outcome (``processed``/``failed``/``lost``)."""

        if outcome not in _OUTCOMES:
            raise ValueError(f"unknown outbox outcome: {outcome}")
        setattr(self, outcome, getattr(self, outcome) + 1)

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "processed": self.processed,
            "failed": self.failed,
            "lost": self.lost,
            "expired": self.expired,
        }


_OUTCOMES = frozenset({"processed", "failed", "lost"})


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Outcome counters of one retention cleanup run."""

    processed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"processed": self.processed, "failed": self.failed}


class OutboxStore(Protocol):
    """Lease and terminal-state operations on the PostgreSQL outbox rows."""

    #: Seconds after which a lease without a heartbeat is considered dead.
    processing_timeout: int
    #: Longest error text persisted on a failed row.
    error_max_length: int

    def claim_batch(self, worker_id: str, limit: int) -> list[OutboxEvent]:
        """Lease up to ``limit`` projectable events for ``worker_id``."""

    def heartbeat(self, event_id: UUID, claim_token: UUID) -> bool:
        """Renew one lease; ``False`` means this worker no longer owns it."""

    def begin_attempt(self, event_id: UUID, claim_token: UUID) -> int | None:
        """Count one attempt and return it, or ``None`` when the lease is gone."""

    def mark_processed(self, event: OutboxEvent) -> bool:
        """Write the terminal ``processed`` state and collapse covered siblings."""

    def mark_failed(self, event_id: UUID, claim_token: UUID, error: str) -> bool:
        """Write the terminal ``failed`` state with a bounded error text."""

    def mark_expired_failed(self, limit: int) -> int:
        """Fail rows whose lease heartbeat expired; return how many."""

    def cleanup(self, batch_size: int) -> dict[str, int]:
        """Delete rows past their retention window; return per-status counts."""


class OutboxProjector(Protocol):
    """Project one authoritative node into Elasticsearch."""

    def warm_up(self) -> None:
        """One-time per-process preparation (index existence checks)."""

    def project(
        self,
        event: OutboxEvent,
        check_claim: Callable[[], None],
    ) -> None:
        """Project ``event``; raise :class:`OutboxClaimLost` to abort early."""


@dataclass(frozen=True, slots=True)
class ConsumeOptions:
    """Tuning of one projection batch."""

    batch_size: int = 100
    #: Per-attempt wall-clock budget handed to the projector's lease probe.
    attempt_timeout_seconds: float = 60.0
    #: Lease renewal cadence while one event is in flight.
    heartbeat_interval_seconds: float = 10.0


__all__ = [
    "MAX_ATTEMPTS",
    "CleanupReport",
    "ConsumeOptions",
    "OutboxClaimLost",
    "OutboxEvent",
    "OutboxProjector",
    "OutboxStore",
    "ProjectionReport",
]