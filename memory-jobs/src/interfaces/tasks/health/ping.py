"""Smoke-test and health tasks.

``memory_jobs.health.ping`` is the canonical example of a task in this
service: synchronous, bound to :class:`src.interfaces.tasks.base.JobTask`, and using the
shared runtime instead of opening its own connections.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from task_bridge.mem_router import TaskName

from src.interfaces.tasks.base import JobTask
from src.runtime import get_runtime


@shared_task(bind=True, base=JobTask, name=TaskName.HEALTH_PING.value)
def ping(self) -> dict[str, Any]:
    """Probe every infrastructure dependency from inside a worker process."""

    return {
        "task_id": self.request.id,
        "dependencies": get_runtime().dependency_health(),
    }
