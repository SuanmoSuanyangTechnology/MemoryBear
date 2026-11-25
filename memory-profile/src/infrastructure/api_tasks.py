"""Direct API-worker task publishing for migrated endpoints.

The API host registers these names in ``api/app/tasks.py``; profile only
publishes them. No gateway task sits in between: when an endpoint's API logic
dispatches Celery work (e.g. the reflection after a merge, or the re-embed
workers after a manual retry), the profile publishes that task itself.

``ApiTaskPublisher`` is separate from ``TaskBridgeManager`` because the two
targets declare their queues differently: memory-jobs declares
``x-max-priority`` and expects the priority buckets, while the API's queues are
declared without it. Publishing to an API queue through the memory-jobs bridge
would trip RabbitMQ's PRECONDITION_FAILED, and on Redis would drop the message
into a priority list the worker's plain BRPOP never reads.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from task_bridge import CeleryBridge
from task_bridge.mem_router import TaskPriority
from task_bridge.transports.celery.config import CeleryConfig

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

#: Registered by the API's ``api/app/tasks.py``; consumed on ``reflection_tasks``.
API_REFLECTION_LAYER2_TASK = "app.tasks.do_layer2_reflection"
API_REFLECTION_QUEUE = "reflection_tasks"

#: Re-embed workers (still API-owned until the API is retired); consumed on
#: ``memory_heavy_tasks``.
API_REEMBED_JOB_TASK = "app.tasks.run_reembed_job"
API_REEMBED_END_USER_TASK = "app.tasks.do_reembed_end_user"
API_REEMBED_QUEUE = "memory_heavy_tasks"

#: Safety-net default for calls that do not name a queue explicitly.
API_TASK_DEFAULT_QUEUE = "memory_tasks"


async def dispatch_layer2_reflection(
    task_bridge: object,
    *,
    end_user_id: uuid.UUID | str,
    config_id: uuid.UUID | str,
    workspace_id: uuid.UUID | str,
) -> str:
    """Publish ``do_layer2_reflection`` straight to the API worker.

    ``from_retry=True`` skips the activity/period gate: a merge must always be
    followed by a reflection.
    """

    return await task_bridge.submit(  # type: ignore[attr-defined]
        API_REFLECTION_LAYER2_TASK,
        kwargs={
            "end_user_id": str(end_user_id),
            "config_id": str(config_id),
            "workspace_id": str(workspace_id),
            "from_retry": True,
        },
        queue=API_REFLECTION_QUEUE,
        priority=TaskPriority.BACKGROUND,
    )


class ApiTaskPublisher:
    """Own the process-wide producer for API-worker tasks (no prio declaration)."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._bridge: CeleryBridge | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._bridge is not None

    def _config(self) -> CeleryConfig:
        base = CeleryConfig.from_env()
        return replace(
            base,
            broker_url=self._settings.task_bridge_broker_url or base.broker_url,
            result_backend=(
                self._settings.task_bridge_result_backend or base.result_backend
            ),
            default_queue=API_TASK_DEFAULT_QUEUE,
            # The API worker's queues are declared without x-max-priority; keep
            # the environment-derived options untouched and add nothing.
            app_options=dict(base.app_options),
        )

    async def bridge(self) -> CeleryBridge:
        bridge = self._bridge
        if bridge is not None:
            return bridge
        async with self._lock:
            if self._bridge is None:
                configured = CeleryBridge(self._config())
                await configured.awarm_up(connections=2)
                self._bridge = configured
            return self._bridge

    async def submit(
        self,
        task_name: str,
        *,
        kwargs: Mapping[str, Any],
        queue: str,
    ) -> str:
        """Publish one task by name; callers never pass a priority."""

        bridge = await self.bridge()
        return await bridge.asubmit(
            task_name,
            kwargs=dict(kwargs),
            queue=queue,
        )

    async def aclose(self) -> None:
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            await bridge.aclose()


async def dispatch_reembed_job(
    publisher: ApiTaskPublisher,
    *,
    job_id: uuid.UUID | str,
) -> str | None:
    """Enqueue the re-embed driver after a job-level retry was committed.

    A publish failure is logged, not raised: the job is already ``running`` and
    the periodic reconciler re-dispatches jobs without a heartbeat.
    """

    try:
        return await publisher.submit(
            API_REEMBED_JOB_TASK,
            kwargs={"job_id": str(job_id)},
            queue=API_REEMBED_QUEUE,
        )
    except Exception as exc:  # noqa: BLE001 - reconciler owns the retry
        logger.error(
            "memory re-embed job dispatch failed: job=%s error=%s",
            job_id,
            exc,
            exc_info=True,
        )
        return None


async def dispatch_reembed_end_user(
    publisher: ApiTaskPublisher,
    *,
    job_id: uuid.UUID | str,
    end_user_id: str,
) -> str | None:
    """Enqueue one end_user's rebuild after its row was reset to ``pending``."""

    try:
        return await publisher.submit(
            API_REEMBED_END_USER_TASK,
            kwargs={"job_id": str(job_id), "end_user_id": end_user_id},
            queue=API_REEMBED_QUEUE,
        )
    except Exception as exc:  # noqa: BLE001 - reconciler owns the retry
        logger.error(
            "memory re-embed end_user dispatch failed: job=%s end_user=%s error=%s",
            job_id,
            end_user_id,
            exc,
            exc_info=True,
        )
        return None


__all__ = [
    "API_REEMBED_END_USER_TASK",
    "API_REEMBED_JOB_TASK",
    "API_REEMBED_QUEUE",
    "API_REFLECTION_LAYER2_TASK",
    "API_REFLECTION_QUEUE",
    "API_TASK_DEFAULT_QUEUE",
    "ApiTaskPublisher",
    "dispatch_layer2_reflection",
    "dispatch_reembed_end_user",
    "dispatch_reembed_job",
]