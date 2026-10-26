"""Smoke-test and health tasks.

``memory_jobs.health.ping`` is the canonical example of a task in this
service: synchronous, bound to :class:`src.tasks.base.JobTask`, and using the
shared runtime instead of opening its own connections.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from src.runtime import get_runtime
from src.tasks.base import JobTask


@shared_task(bind=True, base=JobTask, name="memory_jobs.health.ping")
def ping(self) -> dict[str, Any]:
    """Probe every infrastructure dependency from inside a worker process."""

    return {
        "task_id": self.request.id,
        "dependencies": get_runtime().dependency_health(),
    }
