"""The storage-outbox cleanup job: delete rows past their retention window.

The handler is the application's side of the API's ``app.tasks.cleanup_outbox``
task. It runs one bounded :class:`CleanupOutboxUseCase` drain and returns the
per-status deletion counters. The job is not retried: a missed run only means the
next scheduled run sees the same expired rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.outbox.cleanup import CleanupOutboxUseCase
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.ports.outbox.outbox import CleanupReport


class OutboxCleanupJob:
    """Delete processed and failed outbox rows past their retention windows."""

    spec = JobSpec(resource_class=ResourceClass.BULK, acks_late=False)

    def __init__(self, use_case: CleanupOutboxUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, _payload: Mapping[str, Any], *, attempt_token: str
    ) -> CleanupReport:
        return self._use_case.execute()


__all__ = ["OutboxCleanupJob"]