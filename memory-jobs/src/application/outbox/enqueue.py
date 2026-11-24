"""A2 S4c-4: validate one outbox batch before the adapter inserts it.

The interesting rules are the API producer's, not SQL: every event needs a uuid,
a non-empty label/node id and a known operation; the same event id twice in one
batch is fine only when the business fields also match, otherwise the caller has
a bug and must not get a partial write.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from src.application.ports.outbox.outbox_write import (
    OPERATIONS,
    OutboxEventDraft,
    OutboxWriteError,
    OutboxWriter,
)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutboxWriteError(f"{field} is required")
    return value


class EnqueueOutboxEventsUseCase:
    """Validate and deduplicate one batch, then hand it to the writer."""

    def __init__(self, writer: OutboxWriter) -> None:
        self._writer = writer

    def execute(self, drafts: Sequence[OutboxEventDraft]) -> list[UUID]:
        if not drafts:
            return []

        unique: dict[UUID, OutboxEventDraft] = {}
        for draft in drafts:
            if not isinstance(draft.id, UUID):
                raise OutboxWriteError("event id must be a uuid")
            label = _text(draft.label, "label")
            node_id = _text(draft.node_id, "node_id")
            operation = _text(draft.operation, "operation")
            if operation not in OPERATIONS:
                raise OutboxWriteError(f"unknown outbox operation: {operation}")
            normalised = OutboxEventDraft(
                id=draft.id, label=label, node_id=node_id, operation=operation
            )
            existing = unique.get(normalised.id)
            if existing is not None and existing != normalised:
                raise OutboxWriteError(
                    f"conflicting event id in batch: {normalised.id}"
                )
            unique[normalised.id] = normalised

        ordered = sorted(unique.values(), key=lambda draft: draft.id.int)
        return list(self._writer.enqueue(ordered))


__all__ = ["EnqueueOutboxEventsUseCase"]