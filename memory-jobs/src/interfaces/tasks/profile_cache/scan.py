"""Periodic sweep that dispatches stale insight/summary caches.

This task only renders the application use case; enumeration, freshness rules,
the inflight probe and publishing all live in ``ScanProfileCachesUseCase``.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.composition import SCAN_LANGUAGE, get_container
from src.interfaces.tasks.base import JobTask

#: Enumeration plus dispatch only; the API used the same 10-minute budget.
SCAN_TIME_LIMIT = 600
SCAN_SOFT_TIME_LIMIT = 540


def _use_case():
    return get_container().scan_profile_caches(SCAN_LANGUAGE)


@shared_task(
    name=TaskName.PROFILE_CACHE_SCAN.value,
    base=JobTask,
    bind=True,
    ignore_result=False,
    max_retries=0,
    # A lost scan only means the next nightly run sweeps again; late acks would
    # just re-enumerate a sweep that is safe to repeat.
    acks_late=False,
    time_limit=SCAN_TIME_LIMIT,
    soft_time_limit=SCAN_SOFT_TIME_LIMIT,
)
def scan(self: JobTask) -> dict[str, Any]:
    """Dispatch one ``generate`` unit per user whose cache is out of date."""

    payload = _use_case().execute().to_payload()
    payload["task_id"] = self.request.id
    return payload
