"""Audit consumer: Redis Stream -> consumer group -> batched inserts into audit_logs.

XREADGROUP distributes entries across replicas; inserts are idempotent via
event_id ON CONFLICT, so redelivery after a crash or timeout does not duplicate
rows. Entries left pending by an interrupted consumer are reclaimed with
XAUTOCLAIM on a fixed interval, so a lost reply never strands audit events.
"""
import asyncio
import logging
import os
import socket
import time

from redis.exceptions import RedisError
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from src import db
from src import redis as iredis
from src.config import settings
from src.repositories.audit import insert_audit_logs

logger = logging.getLogger(__name__)

_GROUP = "audit-consumers"
_CONSUMER = f"{socket.gethostname()}:{os.getpid()}"

# Pending entries idle this long are treated as abandoned: long enough that a
# healthy consumer has acked, short enough to bound audit lag after a crash.
_RECLAIM_MIN_IDLE_MS = 60_000
_RECLAIM_INTERVAL_S = 60.0
# Bound one reclaim pass so a large orphaned PEL cannot monopolize the loop.
_RECLAIM_MAX_BATCHES = 5


def _entries_to_items(entries) -> list[dict]:
    # decode_responses=False: field/value are bytes -> normalize to str
    # (jsonb_to_recordset takes text).
    return [
        {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in e[1].items()}
        for e in entries
    ]


async def _persist_entries(session, redis, entries) -> int:
    """Insert a batch, then XACK it; split the batch on data errors.

    insert_audit_logs runs one jsonb_to_recordset statement for the whole
    batch, so a single rejected entry would otherwise keep every healthy entry
    unacked and stuck in the PEL. Bisecting isolates the rejected entry (which
    stays pending for operators) while the rest are persisted and acked.
    Connection-level failures are not data errors: propagate them instead of
    amplifying the statement count.
    """
    try:
        await insert_audit_logs(session, _entries_to_items(entries))
    except (IntegrityError, DataError):
        await session.rollback()
        if len(entries) == 1:
            logger.exception(
                "audit entry %s rejected by the database; leaving it pending",
                entries[0][0],
            )
            return 0
        mid = len(entries) // 2
        return await _persist_entries(session, redis, entries[:mid]) + await _persist_entries(
            session, redis, entries[mid:]
        )
    await redis.xack(settings.AUDIT_STREAM_KEY, _GROUP, *[e[0] for e in entries])
    return len(entries)


async def _ensure_group(redis) -> None:
    try:
        await redis.xgroup_create(settings.AUDIT_STREAM_KEY, _GROUP, id="0", mkstream=True)
    except RedisError:
        pass  # BUSYGROUP（组已存在）或连接错误；后者由调用方捕获


async def _consume_stream(session, redis, batch_size: int) -> int:
    await _ensure_group(redis)
    # block=300ms, far below the 5s socket read timeout: block=0 would stall
    # until the timeout fires and log a spurious TimeoutError every round.
    # Bounded blocking returns nil and paces the loop at 300ms + 1s sleep.
    resp = await redis.xreadgroup(_GROUP, _CONSUMER, {settings.AUDIT_STREAM_KEY: ">"},
                                  count=batch_size, block=300)
    if not resp:
        return 0
    entries = resp[0][1]
    if not entries:
        return 0
    # insert 失败 → 不 XACK（PEL 重投，下轮重试）；成功（含 ON CONFLICT 跳过重复）→ 全量 XACK
    return await _persist_entries(session, redis, entries)


async def reclaim_pending(session, redis, batch_size: int = 100) -> int:
    """Claim entries abandoned by crashed or timed-out consumers and persist them.

    XREADGROUP with ">" only returns never-delivered entries, so anything
    assigned to a consumer that died (or whose reply was lost to a socket
    timeout) would sit in its PEL forever. Claimed entries take the same
    insert-then-XACK path as live ones; min_idle_time keeps in-flight work of
    healthy replicas from being stolen.
    """
    await _ensure_group(redis)
    cursor: str | bytes = "0-0"
    total = 0
    for _ in range(_RECLAIM_MAX_BATCHES):
        resp = await redis.xautoclaim(
            settings.AUDIT_STREAM_KEY,
            _GROUP,
            _CONSUMER,
            min_idle_time=_RECLAIM_MIN_IDLE_MS,
            start_id=cursor,
            count=batch_size,
        )
        if not resp:
            break
        cursor = resp[0]
        entries = resp[1] if len(resp) > 1 else []
        deleted = resp[2] if len(resp) > 2 else []
        if deleted:
            # Trimmed by AUDIT_RETENTION_DAYS before being acked: these events
            # cannot be persisted anymore, so surface the audit gap.
            logger.warning(
                "audit_consumer dropped %d pending event(s) trimmed from the stream: %s",
                len(deleted),
                deleted,
            )
        if not entries:
            break
        total += await _persist_entries(session, redis, entries)
        if cursor in ("0-0", b"0-0"):
            break
    return total


async def consume_once(session, redis, batch_size: int = 100) -> int:
    return await _consume_stream(session, redis, batch_size)


async def audit_loop():
    next_reclaim = 0.0  # reclaim immediately on startup: recovers the previous run's PEL
    while True:
        try:
            async with db.get_async_db_context() as session:
                await consume_once(session, iredis.redis)
                if time.monotonic() >= next_reclaim:
                    # Advance before the call: a failing reclaim (poison batch,
                    # DB outage) must retry on the normal cadence, not every loop.
                    next_reclaim = time.monotonic() + _RECLAIM_INTERVAL_S
                    reclaimed = await reclaim_pending(session, iredis.redis)
                    if reclaimed:
                        logger.info("audit_consumer reclaimed %d pending event(s)", reclaimed)
        except (SQLAlchemyError, RedisError, ValueError, TypeError):
            # Keep the loop alive on DB/Redis/decoding failures: unacked entries
            # stay in the stream or PEL and are retried on the next round.
            logger.exception("audit_consumer failed")
        await asyncio.sleep(1.0)
