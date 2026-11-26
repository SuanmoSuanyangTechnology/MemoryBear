"""Ports for the dispatch-admission worker: unit queues and work dispatching.

The admission layer separates *admitting* a submission from *executing* it. The
producer side (memory-profile) enqueues one message per unit and publishes a
dispatch signal; the worker side claims a unit's head, publishes the real work
task, records the attempt, and finishes it when the task reports back. This
module is the application's vocabulary for that worker side: records, reports
and the two ports, with no Redis or Celery type anywhere.

``unit_key`` is the ordering unit — one queue, one lock and one in-flight
attempt per ``(task_name, user_id)`` pair. The wire keyspace itself lives in the
shared contract (``task_bridge.mem_admission``), which both services install.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

#: Extra work-task kwarg carrying the admitted message id. The task base class
#: reads it back in ``after_return`` to publish the completion advance; a work
#: task published outside the admission layer simply does not carry it.
ADMISSION_MSG_ID_KEY = "_admission_msg_id"


class AttemptStatus(StrEnum):
    """How one admitted attempt ended.

    ``EXPIRED`` is decided by the worker's recovery sweep; the other three are
    the states a finished Celery work task reports.
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"

    @classmethod
    def from_completion(cls, state: object) -> AttemptStatus:
        """Map the terminal state a work task reported.

        Anything else means the caller published a completion for a task that
        has not finished (or used an unknown state name), which is a bug rather
        than a race.
        """

        status = _COMPLETION_STATUS.get(state) if isinstance(state, str) else None
        if status is None:
            raise ValueError(f"unsupported task completion state: {state!r}")
        return status

    @classmethod
    def from_result_backend(cls, state: object) -> AttemptStatus | None:
        """Map a result-backend status; non-terminal states answer ``None``."""

        return _COMPLETION_STATUS.get(state) if isinstance(state, str) else None


#: The terminal states a finished work task reports. Defined after the enum so
#: both class methods share one table.
_COMPLETION_STATUS: dict[str, AttemptStatus] = {
    AttemptStatus.SUCCESS.value: AttemptStatus.SUCCESS,
    AttemptStatus.FAILURE.value: AttemptStatus.FAILURE,
    AttemptStatus.REVOKED.value: AttemptStatus.REVOKED,
}


@dataclass(frozen=True, slots=True)
class QueuedUnitMessage:
    """One admitted message sitting at the head of a unit's queue."""

    msg_id: str
    task_name: str
    user_id: str
    priority: int
    params: Mapping[str, Any]
    #: Broker queue the admitted work must be published to; empty means the
    #: dispatcher uses the shared ``TASK_ROUTES`` table. Producers outside this
    #: service (the API's write tasks) name their own queue because their task
    #: names are not part of this service's routing contract.
    queue: str = ""


@dataclass(frozen=True, slots=True)
class QueueClaim:
    """What one dispatcher got when it tried to claim a unit's head.

    ``reason`` is ``"empty"`` (nothing queued), ``"busy"`` (an attempt holds the
    unit) or ``"contended"`` (another dispatcher holds the claim); it is only
    meaningful when no message came back.
    """

    message: QueuedUnitMessage | None = None
    reason: str = ""

    @property
    def claimed(self) -> bool:
        return self.message is not None


@dataclass(frozen=True, slots=True)
class StoredAttempt:
    """A dispatched attempt recorded under its work task's id."""

    task_id: str
    unit_key: str
    msg_id: str
    task_name: str
    user_id: str
    dispatched_at_ms: int


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Outcome of one dispatch task run.

    ``status`` is ``"DISPATCHED"`` when a work task was published or
    ``"SKIPPED"`` when the unit had nothing claimable; ``task_id`` is then the
    *work* task's id, not the dispatch task's own.
    """

    status: str
    unit_key: str = ""
    task_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecoverReport:
    """Counters of one recovery sweep."""

    status: str = "SUCCESS"
    #: Attempts expired because their task budget ran out.
    expired: int = 0
    #: Attempts finalized from a terminal result-backend state.
    finalized: int = 0
    #: Active units examined while re-arming queued-but-idle ones.
    scanned_units: int = 0
    #: Units woken with a dispatch signal.
    woke: int = 0
    #: Wakes that failed and whose units were put back on the ready set.
    errors: int = 0


class AdmissionStore(Protocol):
    """The worker-side state of the admission layer.

    Every method is an atomic contract operation: ``claim`` and ``commit`` are
    separate so a failed publish can release the claim without recording an
    attempt, and ``finish`` is idempotent (a completion and a recovery sweep may
    race for the same attempt).
    """

    def claim(self, unit_key: str) -> QueueClaim: ...

    def commit(
        self, unit_key: str, message: QueuedUnitMessage, task_id: str
    ) -> None: ...

    def release_claim(self, unit_key: str) -> None: ...

    def attempt(self, task_id: str) -> StoredAttempt | None: ...

    def finish(
        self,
        attempt: StoredAttempt,
        status: AttemptStatus,
        result: Any = None,
        error: str = "",
    ) -> bool: ...

    def attempts(self, limit: int) -> list[StoredAttempt]: ...

    def result_state(self, task_id: str) -> tuple[str, Any] | None: ...

    def rescan_ready(self, unit_limit: int) -> int: ...

    def take_ready(self, limit: int) -> list[str]: ...

    def restore_ready(self, units: list[str]) -> None: ...

    def beat(self) -> None: ...


class JobDispatcher(Protocol):
    """Publish the real work task of an admitted message, and wake units."""

    def dispatch(self, message: QueuedUnitMessage) -> str:
        """Publish one unit's head and return the work task's id."""

    def wake(self, unit_key: str) -> None:
        """Publish a dispatch signal for a unit whose queued work is idle."""


__all__ = [
    "ADMISSION_MSG_ID_KEY",
    "AdmissionStore",
    "AttemptStatus",
    "DispatchReport",
    "JobDispatcher",
    "QueueClaim",
    "QueuedUnitMessage",
    "RecoverReport",
    "StoredAttempt",
]