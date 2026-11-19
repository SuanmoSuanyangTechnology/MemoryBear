"""The only write repository for memory-read side effects.

Two API-owned tables receive bounded DML from this service; their DDL stays in
the API's Alembic chain and memory-profile never migrates them:

- ``memory_short_term`` — the DEEP/NORMAL short-term memo. Its upsert semantics
  are ported verbatim from
  ``api/app/repositories/memory_short_repository.py::upsert_async``: look up by
  ``(end_user_id, messages, aimessages)``, update ``aimessages`` /
  ``search_switch`` / ``retrieved_content`` in place, otherwise insert.
  PostgreSQL has no unique constraint on that lookup key, so (like the host)
  this is a select-then-write rather than ``ON CONFLICT``.
- ``memory_display_records`` — retrieval display cards. One ``RETRIEVE`` row per
  read, batch inserted with ``ON CONFLICT DO NOTHING`` so a retried delivery
  stays idempotent through the partial unique index
  ``uq_memory_display_retrieve_user_operation``
  (``api/app/repositories/memory_display_record_repository.py::
  bulk_insert_retrieved_async``).

The read-only mappings in ``src.models.references`` stay read-only: the
``memory_short_term`` mapping only carries the columns a read needs, so its
write shape is declared here (same names/types as
``api/app/models/memory_short_model.py``). ``MemoryDisplayRecord`` is mapped in
full and is reused as the ``INSERT`` target.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references import MemoryDisplayRecord, ShortTermMemory

logger = logging.getLogger(__name__)

#: The only ``operation`` value this repository writes (the display API also
#: owns ``WRITE`` rows, which belong to the memory-write path).
RETRIEVE_OPERATION = "RETRIEVE"

# ``memory_short_term`` read mapping is intentionally narrow (``id`` /
# ``end_user_id``), so the full write shape lives here instead of widening a
# shared read-only model. Column names/types mirror the API ORM model exactly.

@dataclass(frozen=True, slots=True)
class RetrieveDisplayRecord:
    """One ``RETRIEVE`` display card, fixed before the asynchronous write starts.

    ``id`` / ``operation_id`` / ``occurred_at`` are generated once by the
    adapter, so a retried insert reuses the same values and the unique index
    makes the second attempt a no-op.
    """

    id: UUID
    end_user_id: UUID
    workspace_id: UUID | None
    operation_id: UUID
    search_mode: str
    query: str
    content: str
    occurred_at: datetime

    def to_row(self) -> dict[str, Any]:
        """The exact column set the host writes for a read display card."""

        occurred_at = self.occurred_at
        if occurred_at.tzinfo is not None:
            occurred_at = occurred_at.astimezone(UTC).replace(tzinfo=None)
        return {
            "id": self.id,
            "end_user_id": self.end_user_id,
            "workspace_id": self.workspace_id,
            "operation_id": self.operation_id,
            "operation": RETRIEVE_OPERATION,
            "memory_id": None,
            "memory_type": None,
            "name": None,
            "content": self.content,
            "score": None,
            "rank": None,
            "search_mode": self.search_mode,
            "query": self.query,
            "occurred_at": occurred_at,
        }


class MemoryReadMutationRepository:
    """The two read side-effect writes, and nothing else."""

    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def upsert_short_term(
        self,
        *,
        end_user_id: str,
        messages: str,
        aimessages: str | None,
        search_switch: str | None,
        retrieved_content: Sequence[Mapping[str, Any]],
    ) -> None:
        """Upsert one short-term memo with the host's compound-key semantics.

        ``aimessages`` participates in the lookup with a ``NULL``-aware
        predicate, exactly like ``ShortTermMemoryRepository.upsert_async``: a
        missing answer matches only the rows whose answer is also ``NULL``.
        """

        filters = [
            ShortTermMemory.end_user_id == end_user_id,
            ShortTermMemory.messages == messages,
        ]
        if aimessages is None:
            filters.append(ShortTermMemory.aimessages.is_(None))
        else:
            filters.append(ShortTermMemory.aimessages == aimessages)

        content = [dict(group) for group in retrieved_content]
        async with self.postgres.session() as session:
            existing_id = await session.scalar(
                sa.select(ShortTermMemory.id).where(*filters).limit(1)
            )
            if existing_id is None:
                await session.execute(
                    sa.insert(ShortTermMemory).values(
                        id=uuid4(),
                        end_user_id=end_user_id,
                        messages=messages,
                        aimessages=aimessages,
                        search_switch=search_switch,
                        retrieved_content=content,
                        created_at=utcnow_naive(),
                    )
                )
            else:
                await session.execute(
                    sa.update(ShortTermMemory)
                    .where(ShortTermMemory.id == existing_id)
                    .values(
                        messages=messages,
                        aimessages=aimessages,
                        search_switch=search_switch,
                        retrieved_content=content,
                    )
                )
            await session.commit()

    async def insert_display_records(
        self, records: Sequence[RetrieveDisplayRecord]
    ) -> int:
        """Batch insert ``RETRIEVE`` cards; returns the inserted row count.

        ``ON CONFLICT DO NOTHING`` without an explicit target covers both the
        primary key and the partial unique index, which is what makes a retried
        delivery of the same card a no-op.
        """

        if not records:
            return 0
        rows = [record.to_row() for record in records]
        async with self.postgres.session() as session:
            result = await session.execute(
                pg_insert(MemoryDisplayRecord).values(rows).on_conflict_do_nothing()
            )
            await session.commit()
            return int(result.rowcount or 0)


__all__ = [
    "MemoryReadMutationRepository",
    "RETRIEVE_OPERATION",
    "RetrieveDisplayRecord",
]