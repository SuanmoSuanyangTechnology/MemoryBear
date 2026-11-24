"""Advance one admitted unit by one step.

A dispatch task is woken for one of two reasons:

* a producer admitted a message into an idle unit — the payload carries the
  ``unit_key`` and this run publishes the unit's head;
* a work task finished — the payload carries the finished ``task_id``, this run
  finalizes its attempt, and then publishes the next head of the same unit.

Both paths end in the same place: claim the unit, publish exactly one work task,
record the attempt. The claim is a mutex in Redis, so a second dispatch signal
for the same unit either sees it busy or sees another dispatcher's claim, and
does nothing — duplicate wakes are expected and harmless.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from src.application.ports.admission.admission import (
    AdmissionStore,
    AttemptStatus,
    DispatchReport,
    JobDispatcher,
)

logger = logging.getLogger(__name__)

#: A worker's error text is diagnostic only; the tracker keeps a bounded copy.
MAX_TRACKER_ERROR_LENGTH = 2_000

STATUS_DISPATCHED = "DISPATCHED"
STATUS_SKIPPED = "SKIPPED"

#: Skip reason for a completion whose attempt the recovery sweep already
#: finalized. The pending row (and with it the unit link) is gone, which is a
#: normal race, not a bad payload.
REASON_ATTEMPT_GONE = "attempt_gone"


class DispatchAdmissionUseCase:
    def __init__(self, *, store: AdmissionStore, dispatcher: JobDispatcher) -> None:
        self._store = store
        self._dispatcher = dispatcher

    def execute(self, payload: Mapping[str, Any]) -> DispatchReport:
        finished_task_id = _text(payload.get("task_id"))
        unit_key = ""
        if finished_task_id:
            attempt = self._store.attempt(finished_task_id)
            if attempt is not None:
                self._store.finish(
                    attempt,
                    AttemptStatus.from_completion(payload.get("state")),
                    result=payload.get("result"),
                    error=_bounded_error(payload.get("error")),
                )
                unit_key = attempt.unit_key
            else:
                logger.info(
                    "admission attempt already finalized task_id=%s",
                    finished_task_id,
                    extra={"event": "admission_attempt_already_finalized"},
                )

        # A completion payload names its unit only through the pending attempt;
        # a producer wake names it directly.
        unit_key = unit_key or _text(payload.get("unit_key"))
        if not unit_key:
            return DispatchReport(
                status=STATUS_SKIPPED,
                task_id=finished_task_id,
                reason=REASON_ATTEMPT_GONE,
            )

        claim = self._store.claim(unit_key)
        if claim.message is None:
            return DispatchReport(
                status=STATUS_SKIPPED, unit_key=unit_key, reason=claim.reason
            )

        try:
            task_id = self._dispatcher.dispatch(claim.message)
        except Exception:
            # No attempt was recorded, so the unit head must become claimable
            # again before the exception reaches the executor (which retries).
            self._store.release_claim(unit_key)
            raise

        self._store.commit(unit_key, claim.message, task_id)
        return DispatchReport(
            status=STATUS_DISPATCHED, unit_key=unit_key, task_id=task_id
        )


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _bounded_error(value: Any) -> str:
    error = _text(value)
    return error[:MAX_TRACKER_ERROR_LENGTH]


__all__ = [
    "MAX_TRACKER_ERROR_LENGTH",
    "REASON_ATTEMPT_GONE",
    "STATUS_DISPATCHED",
    "STATUS_SKIPPED",
    "DispatchAdmissionUseCase",
]