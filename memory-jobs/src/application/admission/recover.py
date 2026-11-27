"""Periodic sweep of the dispatch-admission layer.

A crashed or lost worker can leave a unit stuck in two ways: an attempt whose
work task finished while nobody recorded it, and a unit with queued messages but
no running attempt and no pending dispatch signal. The sweep repairs both:

1. write the recovery heartbeat (producers read it to decide the layer is alive);
2. finalize attempts whose work task is terminal in the Celery result backend —
   the completion advance can be lost if the work task died between finishing and
   publishing its completion;
3. expire attempts older than the task budget, so a work task that vanished
   without a result does not pin its unit forever;
4. re-arm active units that have queued work, no lock and no ready marker, and
   wake this sweep's batch of ready units.

Finishing an attempt and re-arming a unit are idempotent, so a sweep racing with
a normal completion advance is safe: the first writer wins and the other call is
a no-op.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from src.application.ports.admission.admission import (
    AdmissionStore,
    AttemptStatus,
    JobDispatcher,
    RecoverReport,
)

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class RecoverAdmissionsUseCase:
    def __init__(
        self,
        *,
        store: AdmissionStore,
        dispatcher: JobDispatcher,
        attempt_timeout_seconds: int,
        pending_batch: int,
        scan_units: int,
        ready_batch: int,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._attempt_timeout_ms = attempt_timeout_seconds * 1000
        self._pending_batch = pending_batch
        self._scan_units = scan_units
        self._ready_batch = ready_batch
        self._clock = clock

    def execute(self) -> RecoverReport:
        self._store.beat()

        expired = 0
        finalized = 0
        now = self._clock()
        for attempt in self._store.attempts(self._pending_batch):
            state = self._store.result_state(attempt.task_id)
            if state is not None:
                status = AttemptStatus.from_result_backend(state[0])
                if status is not None:
                    if self._store.finish(attempt, status, result=state[1]):
                        finalized += 1
                    continue
            if now - attempt.dispatched_at_ms > self._attempt_timeout_ms:
                if self._store.finish(attempt, AttemptStatus.EXPIRED):
                    expired += 1

        scanned_units = self._store.rescan_ready(self._scan_units)
        units = self._store.take_ready(self._ready_batch)

        woke = 0
        errors = 0
        failed_units: list[str] = []
        for unit_key in units:
            try:
                self._dispatcher.wake(unit_key)
            except Exception:
                # Keep the unit on the ready set so the next sweep retries it;
                # one unreachable publish must not cost the other units their run.
                errors += 1
                failed_units.append(unit_key)
                logger.error(
                    "admission wake failed unit=%s",
                    unit_key,
                    exc_info=True,
                    extra={"event": "admission_wake_failed"},
                )
            else:
                woke += 1
        if failed_units:
            self._store.restore_ready(failed_units)

        report = RecoverReport(
            expired=expired,
            finalized=finalized,
            scanned_units=scanned_units,
            woke=woke,
            errors=errors,
        )
        logger.info(
            "admission recovery completed expired=%s finalized=%s scanned_units=%s "
            "woke=%s errors=%s",
            report.expired,
            report.finalized,
            report.scanned_units,
            report.woke,
            report.errors,
            extra={"event": "admission_recover_completed"},
        )
        return report


__all__ = ["RecoverAdmissionsUseCase"]