"""Enqueue outbox events for the shared ES projection.

memory-jobs consumes ``memory_storage_outbox_events``; memory-profile inserts
the events produced by a graph mutation (one per affected node) so the change
reaches Elasticsearch. Insertion is idempotent on the caller's event id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references.outbox_model import OutboxEvent

_CHUNK_SIZE = 1000


class OutboxOperation(StrEnum):
    UPSERT = "upsert"
    DELETE = "delete"
    DRAFT_DELETE = "draft_delete"


class OutboxEnqueueError(RuntimeError):
    """The events could not be persisted; the graph change is already committed."""


class OutboxConflictError(OutboxEnqueueError):
    """An event id was reused with different business fields."""


@dataclass(frozen=True, slots=True)
class OutboxEventInput:
    label: str
    node_id: str
    operation: OutboxOperation = OutboxOperation.UPSERT
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("label must not be blank")
        if not self.node_id or not self.node_id.strip():
            raise ValueError("node_id must not be blank")


def _label_text(label: Any) -> str:
    return label.value if hasattr(label, "value") else str(label)


class OutboxRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self._postgres = postgres

    async def enqueue_many(self, inputs: list[OutboxEventInput]) -> list[uuid.UUID]:
        """Insert the events and verify idempotency; returns the caller's ids."""

        if not inputs:
            return []

        unique: dict[uuid.UUID, OutboxEventInput] = {}
        for item in inputs:
            existing = unique.get(item.id)
            if existing is not None and existing != item:
                raise OutboxConflictError("Conflicting event ID in batch")
            unique[item.id] = item
        # Consistent insert order avoids reversed-batch idempotency deadlocks.
        ordered = sorted(unique.values(), key=lambda item: item.id.int)
        table = OutboxEvent.__table__
        now = utcnow_naive()

        async with self._postgres.session() as session:
            for offset in range(0, len(ordered), _CHUNK_SIZE):
                chunk = ordered[offset : offset + _CHUNK_SIZE]
                await session.execute(
                    insert(table)
                    .values(
                        [
                            {
                                "id": item.id,
                                "label": _label_text(item.label),
                                "node_id": item.node_id,
                                "operation": item.operation.value,
                                "status": "pending",
                                "attempt_count": 0,
                                "created_at": now,
                                "updated_at": now,
                            }
                            for item in chunk
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=[table.c.id])
                )
                rows = (
                    (
                        await session.execute(
                            sa.select(
                                table.c.id,
                                table.c.label,
                                table.c.node_id,
                                table.c.operation,
                            ).where(table.c.id.in_([item.id for item in chunk]))
                        )
                    )
                    .mappings()
                    .all()
                )
                if len(rows) != len(chunk):
                    raise OutboxEnqueueError(
                        "Event removed during idempotency verification"
                    )
                for row in rows:
                    item = unique[row["id"]]
                    if (
                        row["label"],
                        row["node_id"],
                        row["operation"],
                    ) != (
                        _label_text(item.label),
                        item.node_id,
                        item.operation.value,
                    ):
                        raise OutboxConflictError(
                            "Event ID already has different business fields"
                        )
            await session.commit()

        return [item.id for item in inputs]

    async def enqueue(self, events: list[OutboxEventInput]) -> list[uuid.UUID]:
        return await self.enqueue_many(events)


__all__ = [
    "OutboxConflictError",
    "OutboxEnqueueError",
    "OutboxEventInput",
    "OutboxOperation",
    "OutboxRepository",
]