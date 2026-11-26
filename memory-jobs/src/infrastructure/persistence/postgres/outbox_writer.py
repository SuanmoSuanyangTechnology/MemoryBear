"""PostgreSQL writer for the storage-outbox producer (A2 S4c-4).

Mirrors ``api/app/core/memory/storage/outbox/repository.py::_enqueue_many``:

* one transaction per batch, inserts chunked to 1000 rows;
* ``INSERT ... ON CONFLICT (id) DO NOTHING`` makes a replayed batch idempotent;
* sorted by ``id`` so a reversed batch cannot deadlock;
* after the insert the rows are re-read and compared: an id that already exists
  with different business fields is a caller bug, not a safe replay.

The table definition is the read-side ``OUTBOX_EVENTS`` already used by the
projection worker, so producer and consumer cannot drift on column names.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.application.ports.outbox.outbox_write import OutboxEventDraft, OutboxWriteError
from src.infrastructure.outbox.model import OUTBOX_EVENTS

#: Same chunk size as the API producer.
INSERT_CHUNK_SIZE = 1000


class PostgresOutboxWriter:
    """Idempotent batch insert of outbox events."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def enqueue(self, drafts: Sequence[OutboxEventDraft]) -> list[uuid.UUID]:
        if not drafts:
            return []
        unique = {draft.id: draft for draft in drafts}
        ordered = sorted(unique.values(), key=lambda draft: draft.id.int)

        with self._session_factory() as session:
            with session.begin():
                for offset in range(0, len(ordered), INSERT_CHUNK_SIZE):
                    chunk = ordered[offset : offset + INSERT_CHUNK_SIZE]
                    self._insert_chunk(session, chunk)
        return [draft.id for draft in drafts]

    @staticmethod
    def _insert_chunk(session: Session, chunk: list[OutboxEventDraft]) -> None:
        # ``sequence`` is a GENERATED ... AS IDENTITY column (the API model owns
        # it), so it must be omitted; ``created_at``/``updated_at`` are NOT NULL
        # without defaults and have to be supplied here.
        now = datetime.now(UTC).replace(tzinfo=None)
        session.execute(
            pg_insert(OUTBOX_EVENTS)
            .values(
                [
                    {
                        "id": draft.id,
                        "label": draft.label,
                        "node_id": draft.node_id,
                        "operation": draft.operation,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for draft in chunk
                ]
            )
            .on_conflict_do_nothing(index_elements=[OUTBOX_EVENTS.c.id])
        )
        rows = (
            session.execute(
                sa.select(
                    OUTBOX_EVENTS.c.id,
                    OUTBOX_EVENTS.c.label,
                    OUTBOX_EVENTS.c.node_id,
                    OUTBOX_EVENTS.c.operation,
                ).where(OUTBOX_EVENTS.c.id.in_([draft.id for draft in chunk]))
            )
            .mappings()
            .all()
        )
        if len(rows) != len(chunk):
            raise RuntimeError("outbox event disappeared during idempotency check")
        by_id = {draft.id: draft for draft in chunk}
        for row in rows:
            draft = by_id[row["id"]]
            if (
                row["label"],
                row["node_id"],
                row["operation"],
            ) != (draft.label, draft.node_id, draft.operation):
                raise OutboxWriteError(
                    f"event id {draft.id} already has different business fields"
                )


__all__ = ["INSERT_CHUNK_SIZE", "PostgresOutboxWriter"]

