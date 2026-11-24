"""Ports of the storage-outbox producer (A2 S4c-4, commit side).

memory-jobs already owns the outbox *consumer* (Neo4j -> Elasticsearch
projection). The graph-write commit step eventually moves here too, and it must
emit the same rows the API producer writes today: one event per mutated node,
``status=pending``, idempotent by event id.

The producer contract mirrors the API's ``OutboxRepository.enqueue_many``: the
same three operations, the same "same id in a batch must mean the same business
fields" rule, and an insert order sorted by id to avoid reversed-batch deadlocks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

#: Values fixed by the API's ``ck_memory_outbox_operation`` constraint.
OPERATIONS = frozenset({"upsert", "delete", "draft_delete"})


class OutboxWriteError(ValueError):
    """The batch cannot be enqueued as-is (bad fields or conflicting ids)."""


@dataclass(frozen=True, slots=True)
class OutboxEventDraft:
    """One node mutation to project into Elasticsearch."""

    id: UUID
    label: str
    node_id: str
    operation: str = "upsert"


class OutboxWriter(Protocol):
    """Idempotent batch insert into ``memory_storage_outbox_events``."""

    def enqueue(self, drafts: Sequence[OutboxEventDraft]) -> Sequence[UUID]: ...


__all__ = [
    "OPERATIONS",
    "OutboxEventDraft",
    "OutboxWriteError",
    "OutboxWriter",
]