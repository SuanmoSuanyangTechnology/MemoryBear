"""Redis implementation of the dispatch-admission store port.

The keyspace, the Lua scripts and the records come from
``task_bridge.mem_admission`` — the same contract memory-profile writes with, so
this adapter never spells a key or a script itself. Two Redis logical databases
are involved by design: the admission keys live with the business data (shared
with the producer) while work-task results live in the Celery result backend.

Failure policy: every method is a write or a read of coordination state, not of
user data. A Redis outage therefore surfaces as an exception to the caller
(the dispatch/recovery task), which retries or lets the recovery sweep repair
the state later — degrading silently would lose or duplicate work.
"""

from __future__ import annotations

import json
import logging
import socket
from collections.abc import Callable
from typing import Any

from redis import Redis
from task_bridge import mem_admission
from task_bridge.mem_router import TaskPriority

from src.application.ports.admission.admission import (
    AttemptStatus,
    QueueClaim,
    QueuedUnitMessage,
    StoredAttempt,
)

logger = logging.getLogger(__name__)

#: Prefix Celery's Redis result backend gives every stored task meta.
RESULT_META_KEY_PREFIX = "celery-task-meta-"


class RedisAdmissionStore:
    """The admission keyspace and the Celery result backend, behind one port."""

    def __init__(
        self,
        *,
        client_provider: Callable[[], Redis],
        result_client_provider: Callable[[], Redis],
        heartbeat_ttl_seconds: int,
        instance: str | None = None,
    ) -> None:
        self._client_provider = client_provider
        self._result_client_provider = result_client_provider
        self._heartbeat_ttl_seconds = heartbeat_ttl_seconds
        self._instance = instance or socket.gethostname() or "memory-jobs"

    # ------------------------------------------------------------- dispatching

    def claim(self, unit_key: str) -> QueueClaim:
        claim = mem_admission.claim_dispatch(self._client_provider(), unit=unit_key)
        message = claim.message
        if message is None:
            return QueueClaim(reason=claim.reason)
        return QueueClaim(message=_message(message))

    def commit(
        self, unit_key: str, message: QueuedUnitMessage, task_id: str
    ) -> None:
        mem_admission.commit_dispatch(
            self._client_provider(),
            unit=unit_key,
            message=mem_admission.QueuedMessage(
                msg_id=message.msg_id,
                task_name=message.task_name,
                user_id=message.user_id,
                priority=int(message.priority),
                params=dict(message.params),
                queue=message.queue,
            ),
            task_id=task_id,
        )

    def release_claim(self, unit_key: str) -> None:
        mem_admission.release_claim(self._client_provider(), unit=unit_key)

    # ---------------------------------------------------------------- attempts

    def attempt(self, task_id: str) -> StoredAttempt | None:
        raw = self._client_provider().hget(mem_admission.PENDING_KEY, task_id)
        if not raw:
            return None
        record = _parse_attempt(raw, task_id=task_id)
        return _stored(record) if record is not None else None

    def finish(
        self,
        attempt: StoredAttempt,
        status: AttemptStatus,
        result: Any = None,
        error: str = "",
    ) -> bool:
        return mem_admission.finish_attempt(
            self._client_provider(),
            attempt=_record(attempt),
            status=mem_admission.MessageStatus(status.value),
            result=result,
            error=error,
        )

    def attempts(self, limit: int) -> list[StoredAttempt]:
        if limit <= 0:
            return []
        client = self._client_provider()
        stored: list[StoredAttempt] = []
        cursor = 0
        while len(stored) < limit:
            cursor, batch = client.hscan(
                mem_admission.PENDING_KEY, cursor=cursor, count=limit
            )
            for field, raw in batch.items():
                record = _parse_attempt(raw, task_id=str(field))
                if record is not None:
                    stored.append(_stored(record))
            if not cursor:
                break
        return stored[:limit]

    # ----------------------------------------------------------- result state

    def result_state(self, task_id: str) -> tuple[str, Any] | None:
        raw = self._result_client_provider().get(f"{RESULT_META_KEY_PREFIX}{task_id}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "discarding malformed Celery result task_id=%s",
                task_id,
                extra={"event": "admission_result_meta_malformed"},
            )
            return None
        if not isinstance(data, dict):
            return None
        status = data.get("status")
        if not isinstance(status, str) or not status:
            return None
        return status, data.get("result")

    # ------------------------------------------------------------- ready units

    def rescan_ready(self, unit_limit: int) -> int:
        """Re-arm active units that have queued work and no running attempt.

        Units that are empty and unlocked are dropped from the active set as a
        side effect: the producer re-adds a unit when it admits, so a stale
        entry only costs scan slots. The scan is bounded by ``unit_limit``
        because the active set is as large as the deployment's unit count.
        """

        if unit_limit <= 0:
            return 0
        units = self._scan_active_units(unit_limit)
        if not units:
            return 0

        client = self._client_provider()
        reader = client.pipeline(transaction=False)
        for unit_key in units:
            reader.exists(mem_admission.unit_lock_key(unit_key))
            reader.llen(mem_admission.queue_key(unit_key))
        results = reader.execute()

        ready: list[str] = []
        stale: list[str] = []
        for index, unit_key in enumerate(units):
            locked = bool(results[2 * index])
            queued = int(results[2 * index + 1] or 0)
            if locked:
                continue
            if queued > 0:
                ready.append(unit_key)
            else:
                stale.append(unit_key)

        if ready or stale:
            writer = client.pipeline(transaction=False)
            if ready:
                writer.sadd(mem_admission.READY_UNITS_KEY, *ready)
            if stale:
                writer.srem(mem_admission.ACTIVE_UNITS_KEY, *stale)
            writer.execute()
        return len(units)

    def take_ready(self, limit: int) -> list[str]:
        if limit <= 0:
            return []
        members = self._client_provider().spop(
            mem_admission.READY_UNITS_KEY, limit
        )
        return [str(member) for member in members or ()]

    def restore_ready(self, units: list[str]) -> None:
        if not units:
            return
        self._client_provider().sadd(mem_admission.READY_UNITS_KEY, *units)

    # --------------------------------------------------------------- heartbeat

    def beat(self) -> None:
        payload = json.dumps(
            {"instance": self._instance, "at_ms": mem_admission.now_ms()},
            separators=(",", ":"),
        )
        self._client_provider().set(
            mem_admission.HEARTBEAT_KEY, payload, ex=self._heartbeat_ttl_seconds
        )

    # ---------------------------------------------------------------- helpers

    def _scan_active_units(self, limit: int) -> list[str]:
        client = self._client_provider()
        units: list[str] = []
        seen: set[str] = set()
        cursor = 0
        while len(units) < limit:
            cursor, batch = client.sscan(
                mem_admission.ACTIVE_UNITS_KEY, cursor=cursor, count=limit
            )
            for member in batch:
                unit_key = str(member)
                if unit_key not in seen:
                    seen.add(unit_key)
                    units.append(unit_key)
            if not cursor:
                break
        return units[:limit]


def _message(message: mem_admission.QueuedMessage) -> QueuedUnitMessage:
    return QueuedUnitMessage(
        msg_id=message.msg_id,
        task_name=message.task_name,
        user_id=message.user_id,
        priority=int(message.priority),
        params=dict(message.params),
        queue=message.queue,
    )


def _stored(record: mem_admission.AttemptRecord) -> StoredAttempt:
    return StoredAttempt(
        task_id=record.task_id,
        unit_key=record.unit_key,
        msg_id=record.msg_id,
        task_name=record.task_name,
        user_id=record.user_id,
        dispatched_at_ms=int(record.dispatched_at_ms),
    )


def _record(attempt: StoredAttempt) -> mem_admission.AttemptRecord:
    return mem_admission.AttemptRecord(
        task_id=attempt.task_id,
        unit_key=attempt.unit_key,
        msg_id=attempt.msg_id,
        task_name=attempt.task_name,
        user_id=attempt.user_id,
        # The priority is not part of the stored port record; the record is
        # only used to finish the attempt, which reads none of it.
        priority=int(TaskPriority.NORMAL),
        dispatched_at_ms=int(attempt.dispatched_at_ms),
    )


def _parse_attempt(
    raw: Any, *, task_id: str
) -> mem_admission.AttemptRecord | None:
    try:
        return mem_admission.AttemptRecord.from_json(str(raw))
    except (KeyError, TypeError, ValueError):
        # One corrupt pending row must not stop a sweep or a completion advance;
        # the attempt timeout expires it later.
        logger.warning(
            "discarding malformed admission attempt task_id=%s",
            task_id,
            extra={"event": "admission_attempt_malformed"},
        )
        return None


__all__ = ["RESULT_META_KEY_PREFIX", "RedisAdmissionStore"]