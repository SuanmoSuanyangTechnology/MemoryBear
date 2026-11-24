"""Dispatch-admission contract: keyspace, atomic primitives and wire records.

The dispatch admission layer separates *admitting* a unit of memory work from
*executing* it. A producer calls :func:`admit` (or :func:`admit_async`) once per
submission:

* the call is a single Redis Lua script, so the per-unit queue-length gate and
  the per-unit sliding-window QPS gate are decided atomically with the enqueue;
* an admitted message is appended to that unit's queue and the unit is marked
  ready only when no attempt of it is running;
* a separate Celery worker consumes ``memory_jobs.admission.dispatch`` on the
  admission queue, moves the head of a ready unit into the real work task, and
  records the attempt under ``pending``;
* when the work task finishes it wakes the same unit again, so a user's
  messages run one at a time while different users still run in parallel.

``memory-profile`` publishes and reads the producer-facing status through the
same keys and records, which is why they live in this contract package instead
of in either service.

Keyspace (everything under ``dispatch_admission:``)::

    queue:{unit_key}          list   admitted messages, FIFO
    active_units              set    units with something queued or running
    ready_units               set    units whose head may be dispatched now
    pending                   hash   running attempt, task_id -> AttemptRecord
    unit_lock:{unit_key}      str    "claiming" or the dispatched task id
    dispatch_lock:{unit_key}  str    one in-flight dispatch claim per unit
    tracker:{msg_id}          str    TrackerRecord (producer-facing status)
    rate:{unit_key}           zset   sliding-window QPS bucket
    heartbeat                 str    last successful recovery sweep

``unit_key`` is ``f"{task_name}:{user_id}"``: one ordering unit per task and
user. Task names and user ids contain no colon, so the key round-trips through
:func:`parse_unit_key`.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from task_bridge.mem_router import TaskPriority

#: Key prefix for the whole layer; also the scan pattern root for status reads.
KEY_PREFIX = "dispatch_admission"
QUEUE_KEY_PREFIX = f"{KEY_PREFIX}:queue:"
UNIT_LOCK_KEY_PREFIX = f"{KEY_PREFIX}:unit_lock:"
DISPATCH_LOCK_KEY_PREFIX = f"{KEY_PREFIX}:dispatch_lock:"
TRACKER_KEY_PREFIX = f"{KEY_PREFIX}:tracker:"
RATE_LIMIT_KEY_PREFIX = f"{KEY_PREFIX}:rate:"
ACTIVE_UNITS_KEY = f"{KEY_PREFIX}:active_units"
READY_UNITS_KEY = f"{KEY_PREFIX}:ready_units"
PENDING_KEY = f"{KEY_PREFIX}:pending"
HEARTBEAT_KEY = f"{KEY_PREFIX}:heartbeat"
#: ``SCAN`` pattern for every per-unit queue; status reads walk it.
QUEUE_KEY_PATTERN = f"{QUEUE_KEY_PREFIX}*"

#: Per-unit backlog ceiling; ``0`` means unlimited.
DEFAULT_MAX_QUEUE_LEN = 1000
#: Per-unit admissions per second; ``0`` means unlimited.
DEFAULT_QPS = 0.0
QPS_WINDOW_MS = 1000

#: Extra work-task kwarg carrying the admitted message id. The dispatcher adds
#: it when publishing the work task and the producer's completion hook reads it
#: back; a task published outside the admission layer simply does not carry it.
#: Producers in other services (the API) import this so the two sides cannot
#: drift on the keyword name.
ADMISSION_MSG_ID_KEY = "_admission_msg_id"

#: TTL of the producer-facing tracker entry.
TRACKER_TTL_SECONDS = 86_400
#: TTL of a unit lock held by a dispatched task; the recovery sweep expires an
#: attempt older than the task budget, so this only bounds a crashed worker.
UNIT_LOCK_TTL_SECONDS = 3_600
#: TTL of the claim that one dispatch attempt holds while it publishes.
CLAIM_TTL_SECONDS = 300
#: TTL of the guard that keeps two dispatchers off one unit.
DISPATCH_LOCK_TTL_SECONDS = 60
#: Marks a unit lock as "a dispatcher is publishing, no attempt recorded yet".
CLAIMING = "claiming"


class AdmissionOutcome(IntEnum):
    """Result of an admitted submission, from the caller's point of view."""

    #: The message is queued behind a running attempt, or the unit was already
    #: armed; nothing to publish.
    ADMITTED = 1
    #: The message is queued and this call armed the unit; publish a dispatch
    #: signal. Duplicate producers cannot both see this outcome for one
    #: arming, so a burst of admissions produces one wake, not one per message.
    ADMITTED_READY = 2


class AdmissionRejected(Exception):
    """The submission was refused; the message was not queued."""

    #: ``"queue_full"`` or ``"qps"``.
    def __init__(self, reason: str, *, limit: float, retry_after_seconds: int = 1) -> None:
        super().__init__(f"admission rejected: {reason} (limit={limit})")
        self.reason = reason
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds


class MessageStatus(StrEnum):
    """Producer-facing lifecycle of one admitted message."""

    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    #: Not a stored state: the tracker key is gone or never written.
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """One submission: what to queue, and the limits the gate enforces.

    ``queue`` optionally names the broker queue the admitted work must be
    published to. Producers outside memory-jobs (the API's own write tasks)
    need it because the work task does not follow this service's routing table;
    leaving it empty means the dispatcher uses the contract ``TASK_ROUTES``.
    """

    task_name: str
    user_id: str
    params: Mapping[str, Any]
    priority: int = int(TaskPriority.NORMAL)
    max_queue_len: int = DEFAULT_MAX_QUEUE_LEN
    qps: float = DEFAULT_QPS
    tracker_ttl_seconds: int = TRACKER_TTL_SECONDS
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    now_ms: int | None = None
    queue: str = ""

    @property
    def unit_key(self) -> str:
        return unit_key(self.task_name, self.user_id)


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    """One message sitting in a unit's queue."""

    msg_id: str
    task_name: str
    user_id: str
    priority: int
    params: Mapping[str, Any]
    #: Broker queue of the admitted work; empty means "use ``TASK_ROUTES``".
    queue: str = ""

    @property
    def unit_key(self) -> str:
        return unit_key(self.task_name, self.user_id)

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "msg_id": self.msg_id,
            "task_name": self.task_name,
            "user_id": self.user_id,
            "priority": self.priority,
            "params": dict(self.params),
        }
        if self.queue:
            payload["queue"] = self.queue
        # ``default=str`` keeps payloads from producers that carry UUIDs,
        # datetimes or Decimals publishable; the dispatcher then forwards the
        # stringified values, exactly like the previous scheduler did.
        return json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )

    @classmethod
    def from_json(cls, raw: str) -> QueuedMessage:
        data = json.loads(raw)
        return cls(
            msg_id=str(data["msg_id"]),
            task_name=str(data["task_name"]),
            user_id=str(data["user_id"]),
            priority=int(data.get("priority", int(TaskPriority.NORMAL))),
            params=dict(data.get("params") or {}),
            queue=str(data.get("queue") or ""),
        )


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """A dispatched attempt, stored under the work task's id."""

    task_id: str
    unit_key: str
    msg_id: str
    task_name: str
    user_id: str
    priority: int
    dispatched_at_ms: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "task_id": self.task_id,
                "unit_key": self.unit_key,
                "msg_id": self.msg_id,
                "task_name": self.task_name,
                "user_id": self.user_id,
                "priority": self.priority,
                "dispatched_at_ms": self.dispatched_at_ms,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> AttemptRecord:
        data = json.loads(raw)
        return cls(
            task_id=str(data["task_id"]),
            unit_key=str(data["unit_key"]),
            msg_id=str(data.get("msg_id", "")),
            task_name=str(data.get("task_name", "")),
            user_id=str(data.get("user_id", "")),
            priority=int(data.get("priority", int(TaskPriority.NORMAL))),
            dispatched_at_ms=int(data.get("dispatched_at_ms", 0)),
        )


@dataclass(frozen=True, slots=True)
class TrackerRecord:
    """Producer-facing status of one message."""

    status: MessageStatus
    task_id: str = ""
    result: Any = None
    error: str = ""

    def to_json(self) -> str:
        payload: dict[str, Any] = {"status": self.status.value}
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.result is not None:
            payload["result"] = self.result
        if self.error:
            payload["error"] = self.error
        return json.dumps(payload, separators=(",", ":"), default=str)

    @classmethod
    def from_json(cls, raw: str) -> TrackerRecord:
        data = json.loads(raw)
        try:
            status = MessageStatus(str(data.get("status", MessageStatus.NOT_FOUND)))
        except ValueError:
            status = MessageStatus.NOT_FOUND
        return cls(
            status=status,
            task_id=str(data.get("task_id") or ""),
            result=data.get("result"),
            error=str(data.get("error") or ""),
        )


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------


def unit_key(task_name: str, user_id: str) -> str:
    """The ordering key: one queue, one lock and one QPS window per pair."""

    return f"{task_name}:{user_id}"


def parse_unit_key(value: str) -> tuple[str, str]:
    """Split a unit key back into ``(task_name, user_id)``."""

    task_name, separator, user_id = value.rpartition(":")
    if not separator or not task_name or not user_id:
        raise ValueError(f"invalid unit key: {value!r}")
    return task_name, user_id


def queue_key(unit: str) -> str:
    return f"{QUEUE_KEY_PREFIX}{unit}"


def unit_lock_key(unit: str) -> str:
    return f"{UNIT_LOCK_KEY_PREFIX}{unit}"


def dispatch_lock_key(unit: str) -> str:
    return f"{DISPATCH_LOCK_KEY_PREFIX}{unit}"


def tracker_key(msg_id: str) -> str:
    return f"{TRACKER_KEY_PREFIX}{msg_id}"


def rate_limit_key(unit: str) -> str:
    return f"{RATE_LIMIT_KEY_PREFIX}{unit}"


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Lua scripts (single source of truth for every writer)
# ---------------------------------------------------------------------------
#
# The scripts live as `.lua` files next to this module: one file per state
# transition, reviewable and lintable as Lua instead of Python string literals.
# Each file documents its own KEYS/ARGV contract in its header, and
# `_load_lua_script` is the only place that turns a file into the string
# Redis `EVAL` takes.

_LUA_SCRIPTS_DIR = Path(__file__).resolve().parent / "lua_scripts"


def _load_lua_script(name: str) -> str:
    """Read one script from the package's `lua_scripts` folder.

    A missing file means a broken package build (the wheel must carry the
    folder), so fail at import time with the expected path instead of letting
    the first admission attempt fail with an opaque Redis error.
    """

    path = _LUA_SCRIPTS_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"missing Lua script {path}") from exc


#: Atomically enforce the two gates, enqueue, and arm the unit when idle.
ADMISSION_SCRIPT = _load_lua_script("admission.lua")
#: Claim the right to publish one unit's head message.
CLAIM_DISPATCH_SCRIPT = _load_lua_script("claim_dispatch.lua")
#: Record a published attempt and consume the unit's head message.
COMMIT_DISPATCH_SCRIPT = _load_lua_script("commit_dispatch.lua")
#: Release a claim whose publish failed before an attempt was recorded.
RELEASE_CLAIM_SCRIPT = _load_lua_script("release_claim.lua")
#: Finish one attempt, release its lock, and re-arm the unit if more is queued.
FINISH_ATTEMPT_SCRIPT = _load_lua_script("finish_attempt.lua")


# ---------------------------------------------------------------------------
# Admission (producer side)
# ---------------------------------------------------------------------------


def _admission_arguments(
    request: AdmissionRequest,
) -> tuple[list[str], list[str]]:
    unit = request.unit_key
    message = QueuedMessage(
        msg_id=request.msg_id,
        task_name=request.task_name,
        user_id=request.user_id,
        priority=int(request.priority),
        params=request.params,
        queue=request.queue,
    )
    tracker = TrackerRecord(status=MessageStatus.QUEUED)
    keys = [
        queue_key(unit),
        rate_limit_key(unit),
        ACTIVE_UNITS_KEY,
        READY_UNITS_KEY,
        unit_lock_key(unit),
        tracker_key(request.msg_id),
    ]
    args = [
        str(int(request.max_queue_len)),
        str(float(request.qps)),
        str(request.now_ms if request.now_ms is not None else now_ms()),
        str(QPS_WINDOW_MS),
        message.to_json(),
        unit,
        tracker.to_json(),
        request.msg_id,
        str(int(request.tracker_ttl_seconds)),
    ]
    return keys, args


def _outcome(result: int, request: AdmissionRequest) -> AdmissionOutcome:
    if result == -1:
        raise AdmissionRejected("queue_full", limit=request.max_queue_len)
    if result == -2:
        raise AdmissionRejected("qps", limit=request.qps)
    return (
        AdmissionOutcome.ADMITTED_READY
        if result == 2
        else AdmissionOutcome.ADMITTED
    )


def admit(client: Any, request: AdmissionRequest) -> AdmissionOutcome:
    """Atomically admit one submission with a blocking Redis client."""

    keys, args = _admission_arguments(request)
    result = int(client.eval(ADMISSION_SCRIPT, len(keys), *keys, *args))
    return _outcome(result, request)


async def admit_async(client: Any, request: AdmissionRequest) -> AdmissionOutcome:
    """Atomically admit one submission with an asyncio Redis client."""

    keys, args = _admission_arguments(request)
    result = int(await client.eval(ADMISSION_SCRIPT, len(keys), *keys, *args))
    return _outcome(result, request)


# ---------------------------------------------------------------------------
# Dispatch primitives (worker side)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Claim:
    """One dispatcher's view of a unit after trying to claim its head."""

    message: QueuedMessage | None = None
    #: ``"empty"``, ``"busy"`` (an attempt holds the unit) or ``"contended"``
    #: (another dispatcher holds the claim).
    reason: str = ""

    @property
    def claimed(self) -> bool:
        return self.message is not None


def claim_dispatch(
    client: Any,
    *,
    unit: str,
    owner: str | None = None,
    dispatch_ttl: int = DISPATCH_LOCK_TTL_SECONDS,
    claim_ttl: int = CLAIM_TTL_SECONDS,
) -> Claim:
    """Claim a unit's head message without publishing anything yet."""

    token = owner or uuid.uuid4().hex
    result = client.eval(
        CLAIM_DISPATCH_SCRIPT,
        3,
        dispatch_lock_key(unit),
        unit_lock_key(unit),
        queue_key(unit),
        token,
        str(int(dispatch_ttl)),
        str(int(claim_ttl)),
    )
    if result == -1:
        return Claim(reason="busy")
    if result == -2 or result is None:
        return Claim(reason="empty")
    if result == 0:
        return Claim(reason="contended")
    return Claim(message=QueuedMessage.from_json(str(result)))


def commit_dispatch(
    client: Any,
    *,
    unit: str,
    message: QueuedMessage,
    task_id: str,
    lock_ttl: int = UNIT_LOCK_TTL_SECONDS,
    tracker_ttl: int = TRACKER_TTL_SECONDS,
    dispatched_at_ms: int | None = None,
) -> None:
    """Record the published attempt and consume the unit's head message."""

    attempt = AttemptRecord(
        task_id=task_id,
        unit_key=unit,
        msg_id=message.msg_id,
        task_name=message.task_name,
        user_id=message.user_id,
        priority=int(message.priority),
        dispatched_at_ms=(
            dispatched_at_ms if dispatched_at_ms is not None else now_ms()
        ),
    )
    tracker = TrackerRecord(status=MessageStatus.DISPATCHED, task_id=task_id)
    client.eval(
        COMMIT_DISPATCH_SCRIPT,
        6,
        queue_key(unit),
        unit_lock_key(unit),
        PENDING_KEY,
        tracker_key(message.msg_id),
        dispatch_lock_key(unit),
        READY_UNITS_KEY,
        task_id,
        str(int(lock_ttl)),
        attempt.to_json(),
        tracker.to_json(),
        str(int(tracker_ttl)),
        unit,
    )


def release_claim(client: Any, *, unit: str) -> None:
    """Undo a claim whose publish failed so the unit can be retried."""

    client.eval(
        RELEASE_CLAIM_SCRIPT,
        3,
        dispatch_lock_key(unit),
        unit_lock_key(unit),
        READY_UNITS_KEY,
        unit,
    )


def finish_attempt(
    client: Any,
    *,
    attempt: AttemptRecord,
    status: MessageStatus,
    result: Any = None,
    error: str = "",
    tracker_ttl: int = TRACKER_TTL_SECONDS,
) -> bool:
    """Finish a running attempt and wake its unit when more work is queued."""

    tracker = TrackerRecord(
        status=status,
        task_id=attempt.task_id,
        result=result,
        error=error,
    )
    finished = client.eval(
        FINISH_ATTEMPT_SCRIPT,
        5,
        PENDING_KEY,
        unit_lock_key(attempt.unit_key),
        tracker_key(attempt.msg_id),
        READY_UNITS_KEY,
        queue_key(attempt.unit_key),
        attempt.task_id,
        attempt.unit_key,
        tracker.to_json(),
        str(int(tracker_ttl)),
    )
    return bool(int(finished or 0))


__all__ = [
    "ACTIVE_UNITS_KEY",
    "ADMISSION_MSG_ID_KEY",
    "ADMISSION_SCRIPT",
    "CLAIMING",
    "CLAIM_DISPATCH_SCRIPT",
    "CLAIM_TTL_SECONDS",
    "COMMIT_DISPATCH_SCRIPT",
    "DEFAULT_MAX_QUEUE_LEN",
    "DEFAULT_QPS",
    "DISPATCH_LOCK_KEY_PREFIX",
    "DISPATCH_LOCK_TTL_SECONDS",
    "FINISH_ATTEMPT_SCRIPT",
    "HEARTBEAT_KEY",
    "KEY_PREFIX",
    "PENDING_KEY",
    "QPS_WINDOW_MS",
    "QUEUE_KEY_PATTERN",
    "QUEUE_KEY_PREFIX",
    "RATE_LIMIT_KEY_PREFIX",
    "READY_UNITS_KEY",
    "RELEASE_CLAIM_SCRIPT",
    "TRACKER_KEY_PREFIX",
    "TRACKER_TTL_SECONDS",
    "UNIT_LOCK_KEY_PREFIX",
    "UNIT_LOCK_TTL_SECONDS",
    "AdmissionOutcome",
    "AdmissionRejected",
    "AdmissionRequest",
    "AttemptRecord",
    "Claim",
    "MessageStatus",
    "QueuedMessage",
    "TrackerRecord",
    "admit",
    "admit_async",
    "claim_dispatch",
    "commit_dispatch",
    "dispatch_lock_key",
    "finish_attempt",
    "now_ms",
    "parse_unit_key",
    "queue_key",
    "rate_limit_key",
    "release_claim",
    "tracker_key",
    "unit_key",
    "unit_lock_key",
]