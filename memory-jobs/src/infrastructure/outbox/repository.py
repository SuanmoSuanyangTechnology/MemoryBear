"""Synchronous PostgreSQL implementation of the storage-outbox store port.

The API keeps the producer; this adapter is the consumer side. It mirrors the
API's ``OutboxRepository`` statement for statement, because during the two-phase
rollout both services run at once against the same rows: a lease taken here must
be respected there and vice versa, which only holds if the claim predicate, the
ownership predicate and the terminal state are identical. Everything is
synchronous on purpose — this service exists to run Celery tasks on blocking
clients.

``PostgresOutboxStore`` owns no connection: the sessionmaker is handed in by the
composition root (see :class:`src.infrastructure.postgres.PostgresManager`), and
each method opens its own short transaction with ``sessions.begin()``. A failed
statement therefore rolls back that one transaction and surfaces to the caller,
which retries or lets the lease expire; degrading silently would lose a
projection or leave a row leased forever.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.application.ports.outbox.outbox import MAX_ATTEMPTS, OutboxEvent
from src.infrastructure.outbox.model import (
    DELETE,
    FAILED,
    OUTBOX_EVENTS,
    PENDING,
    PROCESSED,
    PROCESSING,
    PROCESSING_NODE_UNIQUE_INDEX,
)

#: The table this adapter leases and terminates; statements below name every
#: column through it so a schema change fails at import, not at 3 a.m.
events = OUTBOX_EVENTS

#: Error recorded by :meth:`PostgresOutboxStore.mark_expired_failed`; the API
#: writes the same marker so operators can tell an expired lease from a
#: projection failure.
EXPIRED_ERROR = "ProcessingHeartbeatExpired"


def db_now():
    """The database clock, as an SQL expression.

    All lease deadlines compare against ``clock_timestamp()`` rather than
    ``now()``: ``now()`` is the transaction start time, which would make a
    heartbeat written in a long-running transaction look older than it is.
    ``timezone('utc', ...)`` pins the naive-UTC convention the columns store.
    """

    return sa.func.timezone("utc", sa.func.clock_timestamp())


class PostgresOutboxStore:
    """Lease and terminal-state transitions on ``memory_storage_outbox_events``.

    The ownership predicate is the whole contract: a row belongs to this worker
    while its ``claim_token`` matches *and* its last heartbeat is inside
    ``processing_timeout``. Every mutating statement carries that predicate, so
    a lease taken over by another worker (or expired) turns a late write into a
    zero-rowcount result instead of a corrupted state.
    """

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        processing_timeout: int = 300,
        retention_days: int = 30,
        failed_retention_days: int = 60,
        error_max_length: int = 4096,
    ) -> None:
        if not 30 <= processing_timeout <= 3600:
            raise ValueError("processing_timeout must be between 30 and 3600 seconds")
        if not all(1 <= days <= 3650 for days in (retention_days, failed_retention_days)):
            raise ValueError("retention must be between 1 and 3650 days")
        if not 64 <= error_max_length <= 16384:
            raise ValueError("error_max_length must be between 64 and 16384")
        self.sessions = sessions
        self.processing_timeout = processing_timeout
        self.retention_days = retention_days
        self.failed_retention_days = failed_retention_days
        self.error_max_length = error_max_length

    # ------------------------------------------------------------------ claims

    def claim_batch(self, worker_id: str, limit: int) -> list[OutboxEvent]:
        """Lease up to ``limit`` projectable events for ``worker_id``.

        A row is projectable when it is ``pending``, no row of the same
        ``(label, node_id)`` is being projected, and no earlier pending row of
        that node is still queued: projection is per-node ordered, so a newer
        mutation may be skipped only by a still newer one, never by an older
        one. The candidate set is locked with ``SKIP LOCKED`` so concurrent
        workers take disjoint batches instead of queueing behind each other.

        The partial unique index is the second arbiter: the select can return a
        row another consumer claimed in the same instant, and the nested update
        then fails on that index only. That one failure is expected and skips
        the row; every other integrity error is a real bug and propagates.
        """

        if not worker_id or len(worker_id) > 128 or not 1 <= limit <= 1000:
            raise ValueError("Invalid worker ID or claim batch size")
        other = events.alias("other_event")
        has_processing = sa.exists(
            sa.select(1).where(
                other.c.label == events.c.label,
                other.c.node_id == events.c.node_id,
                other.c.status == PROCESSING,
            )
        )
        # "No earlier pending row for this node" keeps the per-node queue serial.
        has_earlier_pending = sa.exists(
            sa.select(1).where(
                other.c.label == events.c.label,
                other.c.node_id == events.c.node_id,
                other.c.status == PENDING,
                other.c.sequence < events.c.sequence,
            )
        )
        # The node's queue tip bounds what this projection already covers. It has
        # to be read by the claim statement itself: a row enqueued between two
        # statements could have committed its Neo4j write after our read, and
        # superseding it would drop an update.
        node_queue = events.alias("node_queue")
        queue_tip = (
            sa.select(sa.func.max(node_queue.c.sequence))
            .where(
                node_queue.c.label == events.c.label,
                node_queue.c.node_id == events.c.node_id,
                node_queue.c.status == PENDING,
            )
            .scalar_subquery()
        )
        claimed: list[OutboxEvent] = []
        with self.sessions.begin() as session:
            rows = (
                session.execute(
                    sa.select(events, queue_tip.label("queue_tip"))
                    .where(
                        events.c.status == PENDING,
                        ~has_processing,
                        ~has_earlier_pending,
                    )
                    .order_by(events.c.sequence)
                    .limit(limit)
                    .with_for_update(skip_locked=True, of=events)
                )
                .mappings()
                .all()
            )
            for row in rows:
                token = uuid4()
                try:
                    with session.begin_nested():
                        result = session.execute(
                            sa.update(events)
                            .where(
                                events.c.id == row["id"],
                                events.c.status == PENDING,
                            )
                            .values(
                                status=PROCESSING,
                                locked_by=worker_id,
                                claim_token=token,
                                locked_at=db_now(),
                                heartbeat_at=db_now(),
                                updated_at=db_now(),
                            )
                        )
                except IntegrityError as exc:
                    if PROCESSING_NODE_UNIQUE_INDEX not in str(exc.orig):
                        raise
                    continue
                if result.rowcount:
                    claimed.append(
                        OutboxEvent(
                            id=row["id"],
                            sequence=row["sequence"],
                            label=row["label"],
                            node_id=row["node_id"],
                            operation=row["operation"],
                            attempt_count=row["attempt_count"],
                            claim_token=token,
                            queue_tip=row["queue_tip"] or row["sequence"],
                        )
                    )
        return claimed

    # ------------------------------------------------------------------- lease

    def _owner(self, event_id: UUID, token: UUID):
        """The predicate that makes a row belong to this worker, in SQL."""

        return sa.and_(
            events.c.id == event_id,
            events.c.status == PROCESSING,
            events.c.claim_token == token,
            events.c.heartbeat_at
            > db_now() - timedelta(seconds=self.processing_timeout),
        )

    def heartbeat(self, event_id: UUID, claim_token: UUID) -> bool:
        """Renew one lease; ``False`` means this worker no longer owns it."""

        with self.sessions.begin() as session:
            result = session.execute(
                sa.update(events)
                .where(self._owner(event_id, claim_token))
                .values(heartbeat_at=db_now(), updated_at=db_now())
            )
            return bool(result.rowcount)

    def begin_attempt(self, event_id: UUID, claim_token: UUID) -> int | None:
        """Count one attempt and return it, or ``None`` when the lease is gone.

        The attempt is counted before the projection runs, so a crash mid-way
        still consumes budget; the cap is enforced here, in the same statement
        that increments, so two processes can never spend more than
        :data:`MAX_ATTEMPTS` between them.
        """

        with self.sessions.begin() as session:
            return (
                session.execute(
                    sa.update(events)
                    .where(
                        self._owner(event_id, claim_token),
                        events.c.attempt_count < MAX_ATTEMPTS,
                    )
                    .values(
                        attempt_count=events.c.attempt_count + 1,
                        updated_at=db_now(),
                    )
                    .returning(events.c.attempt_count)
                )
                .scalar_one_or_none()
            )

    # --------------------------------------------------------------- terminals

    @staticmethod
    def _terminal(status: str, error: str | None = None) -> dict[str, object]:
        """The terminal-state column set: lease cleared, timestamps set apart."""

        return dict(
            status=status,
            locked_by=None,
            claim_token=None,
            locked_at=None,
            heartbeat_at=None,
            updated_at=db_now(),
            last_error=error,
            processed_at=db_now() if status == PROCESSED else None,
            failed_at=db_now() if status == FAILED else None,
        )

    def mark_processed(self, event: OutboxEvent) -> bool:
        """Write the terminal ``processed`` state, then collapse its node's queue.

        The projection re-reads Neo4j, so every row of that node which was
        already pending at claim time is covered by the state that was just
        written: they can all converge to ``processed`` without projecting.
        """

        return self._finish(event.id, event.claim_token, PROCESSED, None, event)

    def mark_failed(self, event_id: UUID, claim_token: UUID, error: str) -> bool:
        """Write the terminal ``failed`` state with a bounded error text.

        The truncation is part of the API contract: the column is ``Text`` but
        operators read it from logs and dashboards, and an unbounded driver
        message (stack traces, query text) would blow up those consumers.
        """

        return self._finish(
            event_id, claim_token, FAILED, error[: self.error_max_length]
        )

    def _finish(
        self,
        event_id: UUID,
        token: UUID,
        status: str,
        error: str | None,
        claim: OutboxEvent | None = None,
    ) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(
                sa.update(events)
                .where(self._owner(event_id, token))
                .values(**self._terminal(status, error))
            )
            # Supersede only after an acknowledged success. A zero rowcount means
            # the lease moved to another worker: collapsing the queue then would
            # discard rows that worker still has to project.
            if result.rowcount and status == PROCESSED and claim is not None:
                self._supersede(session, claim)
            return bool(result.rowcount)

    @staticmethod
    def _supersede(session: Session, claim: OutboxEvent) -> int:
        """Collapse the pending rows this projection already covers.

        A physical delete is projected blindly (no Neo4j read), so it may only
        collapse other deletes: a later state event could be the re-creation of
        the same node id and must still be projected.
        """

        if claim.queue_tip <= 0:
            return 0
        scope = [
            events.c.label == claim.label,
            events.c.node_id == claim.node_id,
            events.c.status == PENDING,
            events.c.sequence <= claim.queue_tip,
        ]
        if claim.operation == DELETE:
            scope.append(events.c.operation == DELETE)
        return session.execute(
            sa.update(events)
            .where(*scope)
            .values(**PostgresOutboxStore._terminal(PROCESSED))
        ).rowcount

    # ------------------------------------------------------------ maintenance

    def mark_expired_failed(self, limit: int) -> int:
        """Fail rows whose lease heartbeat expired; return how many.

        Expiry is decided from the database clock, not from this process's
        clock: a worker with skewed time must not be able to expire a live
        lease. The rows are locked ``SKIP LOCKED`` so two maintenance runs do
        not duplicate the update.
        """

        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.sessions.begin() as session:
            expired = (
                session.execute(
                    sa.select(events.c.id)
                    .where(
                        events.c.status == PROCESSING,
                        events.c.heartbeat_at
                        <= db_now() - timedelta(seconds=self.processing_timeout),
                    )
                    .order_by(events.c.heartbeat_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
                .scalars()
                .all()
            )
            if not expired:
                return 0
            result = session.execute(
                sa.update(events)
                .where(events.c.id.in_(expired))
                .values(**self._terminal(FAILED, EXPIRED_ERROR))
            )
            return result.rowcount

    def cleanup(self, batch_size: int) -> dict[str, int]:
        """Delete rows past their retention window; return per-status counts.

        Processed and failed rows have separate windows (a failure is kept
        longer because it is the only trace of a projection that never landed).
        Deleting an already-``NULL``-timestamped row is impossible by the
        terminal-time constraint, so each window only ever looks at its own
        status.
        """

        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        counts: dict[str, int] = {}
        with self.sessions.begin() as session:
            for status, column, days in (
                (PROCESSED, events.c.processed_at, self.retention_days),
                (FAILED, events.c.failed_at, self.failed_retention_days),
            ):
                ids = (
                    session.execute(
                        sa.select(events.c.id)
                        .where(
                            events.c.status == status,
                            column < db_now() - timedelta(days=days),
                        )
                        .order_by(column)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                    .scalars()
                    .all()
                )
                if ids:
                    result = session.execute(
                        sa.delete(events).where(events.c.id.in_(ids))
                    )
                    counts[status] = result.rowcount
                else:
                    counts[status] = 0
        return counts


__all__ = ["EXPIRED_ERROR", "PostgresOutboxStore", "db_now"]
