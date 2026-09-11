"""Short synchronous PG transactions exposed through a non-blocking async facade."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.memory.storage.outbox.exceptions import OutboxConflictError
from app.core.memory.storage.outbox.types import (
    MAX_ATTEMPTS,
    ClaimedEvent,
    OutboxEventInput,
)
from app.db import SessionLocal
from app.models.outbox_model import OutboxEvent

events = OutboxEvent.__table__


def db_now():
    return sa.func.timezone("utc", sa.func.clock_timestamp())


def create_repository() -> "OutboxRepository":
    """Build the default repository from the project's synchronous DB pool."""
    return OutboxRepository(
        SessionLocal,
        processing_timeout=settings.OUTBOX_PROCESSING_TIMEOUT_SECONDS,
        retention_days=settings.OUTBOX_RETENTION_DAYS,
        failed_retention_days=settings.OUTBOX_FAILED_RETENTION_DAYS,
        error_max_length=settings.OUTBOX_ERROR_MAX_LENGTH,
    )


class OutboxRepository:
    def __init__(self, sessions: sessionmaker[Session], *,
                 processing_timeout: int = 300, retention_days: int = 30,
                 failed_retention_days: int = 60, error_max_length: int = 4096):
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

    async def enqueue_many(self, inputs: list[OutboxEventInput]) -> list[UUID]:
        if not inputs:
            return []
        return await asyncio.to_thread(self._enqueue_many, inputs)

    def _enqueue_many(self, inputs: list[OutboxEventInput]) -> list[UUID]:
        unique = {}
        for item in inputs:
            if item.id in unique and unique[item.id] != item:
                raise OutboxConflictError("Conflicting event ID in batch")
            unique[item.id] = item
        # Consistent insert order avoids reversed-batch idempotency deadlocks.
        ordered = sorted(unique.values(), key=lambda item: item.id.int)
        with self.sessions.begin() as session:
            for offset in range(0, len(ordered), 1000):
                chunk = ordered[offset:offset + 1000]
                session.execute(insert(events).values([
                    {"id": item.id, "label": item.label.value, "node_id": item.node_id,
                     "operation": item.operation.value} for item in chunk
                ]).on_conflict_do_nothing(index_elements=[events.c.id]))
                rows = session.execute(sa.select(
                    events.c.id, events.c.label, events.c.node_id, events.c.operation,
                ).where(events.c.id.in_([item.id for item in chunk]))).mappings().all()
                if len(rows) != len(chunk):
                    raise RuntimeError("Event removed during idempotency verification")
                for row in rows:#若调用方确定不传入id，有默认uuid生成，可优化此处
                    item = unique[row["id"]]
                    if (row["label"], row["node_id"], row["operation"]) != (
                            item.label.value, item.node_id, item.operation.value):
                        raise OutboxConflictError("Event ID already has different business fields")
        return [item.id for item in inputs]

    async def claim_batch(self, worker_id: str, limit: int) -> list[ClaimedEvent]:
        if not worker_id or len(worker_id) > 128 or not 1 <= limit <= 1000:
            raise ValueError("Invalid worker ID or claim batch size")
        return await asyncio.to_thread(self._claim_batch, worker_id, limit)

    def _claim_batch(self, worker_id: str, limit: int) -> list[ClaimedEvent]:
        other = events.alias("other_event")
        blocked = sa.exists(sa.select(1).where(
            other.c.label == events.c.label, other.c.node_id == events.c.node_id,
            sa.or_(other.c.status == "processing", sa.and_(
                other.c.status == "pending", other.c.sequence < events.c.sequence,
            )),
        ))
        claimed = []
        with self.sessions.begin() as session:
            rows = session.execute(sa.select(events).where(
                events.c.status == "pending", ~blocked,
            ).order_by(events.c.sequence).limit(limit).with_for_update(
                skip_locked=True, of=events,
            )).mappings().all()
            for row in rows:
                token = uuid4()
                try:
                    with session.begin_nested():
                        result = session.execute(sa.update(events).where(
                            events.c.id == row["id"], events.c.status == "pending",
                        ).values(status="processing", locked_by=worker_id, claim_token=token,
                                 locked_at=db_now(), heartbeat_at=db_now(), updated_at=db_now()))
                except IntegrityError as exc:
                    if "uq_memory_outbox_processing_node" not in str(exc.orig):
                        raise
                    continue
                if result.rowcount:
                    claimed.append(ClaimedEvent(
                        id=row["id"], sequence=row["sequence"], label=row["label"],
                        node_id=row["node_id"], operation=row["operation"],
                        attempt_count=row["attempt_count"], claim_token=token,
                    ))
        return claimed

    def _owner(self, event_id: UUID, token: UUID):
        return sa.and_(
            events.c.id == event_id, events.c.status == "processing", events.c.claim_token == token,
            events.c.heartbeat_at > db_now() - timedelta(seconds=self.processing_timeout),
        )

    async def heartbeat(self, event_id: UUID, claim_token: UUID) -> bool:
        return await asyncio.to_thread(self._heartbeat, event_id, claim_token)

    def _heartbeat(self, event_id: UUID, claim_token: UUID) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(sa.update(events).where(
                self._owner(event_id, claim_token),
            ).values(heartbeat_at=db_now(), updated_at=db_now()))
            return bool(result.rowcount)

    async def begin_attempt(self, event_id: UUID, claim_token: UUID) -> int | None:
        return await asyncio.to_thread(self._begin_attempt, event_id, claim_token)

    def _begin_attempt(self, event_id: UUID, claim_token: UUID) -> int | None:
        with self.sessions.begin() as session:
            return session.execute(sa.update(events).where(
                self._owner(event_id, claim_token), events.c.attempt_count < MAX_ATTEMPTS,
            ).values(attempt_count=events.c.attempt_count + 1, updated_at=db_now())
                .returning(events.c.attempt_count)).scalar_one_or_none()

    @staticmethod
    def _terminal(status: str, error: str | None = None) -> dict:
        return dict(status=status, locked_by=None, claim_token=None, locked_at=None,
                    heartbeat_at=None, updated_at=db_now(), last_error=error,
                    processed_at=db_now() if status == "processed" else None,
                    failed_at=db_now() if status == "failed" else None)

    async def mark_processed(self, event_id: UUID, claim_token: UUID) -> bool:
        return await self._finish(event_id, claim_token, "processed", None)

    async def mark_failed(self, event_id: UUID, claim_token: UUID, error: str) -> bool:
        return await self._finish(event_id, claim_token, "failed", error[:self.error_max_length])

    async def _finish(self, event_id, token, status, error) -> bool:
        return await asyncio.to_thread(self._finish_sync, event_id, token, status, error)

    def _finish_sync(self, event_id, token, status, error) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(sa.update(events).where(
                self._owner(event_id, token),
            ).values(**self._terminal(status, error)))
            return bool(result.rowcount)

    async def mark_expired_failed(self, limit: int) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return await asyncio.to_thread(self._mark_expired_failed, limit)

    def _mark_expired_failed(self, limit: int) -> int:
        with self.sessions.begin() as session:
            expired = session.execute(sa.select(events.c.id).where(
                events.c.status == "processing",
                events.c.heartbeat_at <= db_now() - timedelta(seconds=self.processing_timeout),
            ).order_by(events.c.heartbeat_at).limit(limit).with_for_update(skip_locked=True)).scalars().all()
            if not expired:
                return 0
            result = session.execute(sa.update(events).where(events.c.id.in_(expired)).values(
                **self._terminal("failed", "ProcessingHeartbeatExpired"[:self.error_max_length]),
            ))
            return result.rowcount

    async def cleanup(self, batch_size: int) -> dict[str, int]:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        return await asyncio.to_thread(self._cleanup, batch_size)

    def _cleanup(self, batch_size: int) -> dict[str, int]:
        counts = {}
        with self.sessions.begin() as session:
            for status, column, days in (
                ("processed", events.c.processed_at, self.retention_days),
                ("failed", events.c.failed_at, self.failed_retention_days),
            ):
                ids = session.execute(sa.select(events.c.id).where(
                    events.c.status == status, column < db_now() - timedelta(days=days),
                ).order_by(column).limit(batch_size).with_for_update(skip_locked=True)).scalars().all()
                if ids:
                    result = session.execute(sa.delete(events).where(events.c.id.in_(ids)))
                    counts[status] = result.rowcount
                else:
                    counts[status] = 0
        return counts
