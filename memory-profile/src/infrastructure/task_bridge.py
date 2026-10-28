"""Publish generation tasks by name and wait for their results.

The workers live in ``memory-jobs``; this process keeps its request/response
shape and owns no Celery application. ``task-bridge-sdk`` is exactly that seam:
it builds a bare producer (broker + result backend, JSON protocol), publishes by
task name, and reads the result backend — without importing the worker's module
or registering anything. The conversation with Celery stays behind this class so
the service layer only sees "submit, wait, get a status".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from task_bridge import CeleryBridge, TaskStatus
from task_bridge.mem_router import MAX_PRIORITY, PRIORITY_STEPS, QueueName, TaskPriority
from task_bridge.transports.celery.config import CeleryConfig

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

class TaskBridgeManager:
    """Own the process-wide bridge, created lazily on the running event loop."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._bridge: CeleryBridge | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._bridge is not None

    def _config(self) -> CeleryConfig:
        """Env-derived broker/backend, with this service's queue policy applied.

        ``from_env`` does the URL resolution (TASK_BRIDGE_* → CELERY_* → Redis
        DB 3/4) so producers and workers agree; this service only adds the queue
        it publishes into and the priority buckets the worker reads. Task name,
        queue and routing table come from ``task_bridge.mem_router``, the contract
        memory-jobs installs too.
        """

        base = CeleryConfig.from_env()
        broker_url = self._settings.task_bridge_broker_url or base.broker_url
        app_options: dict[str, Any] = {
            **base.app_options,
            # The producer declares the queue when it first publishes, so it has
            # to declare it exactly like the worker does: RabbitMQ rejects a
            # declaration whose ``x-max-priority`` differs (PRECONDITION_FAILED),
            # and without the argument it drops the priority entirely.
            "task_queue_max_priority": MAX_PRIORITY,
        }
        if broker_url.startswith(("redis://", "rediss://")):
            # Redis-only: kombu keeps one list per priority step there, so a
            # producer using different steps writes where the worker's BRPOP
            # never looks. AMQP orders by the message property instead.
            app_options["broker_transport_options"] = {
                **dict(base.app_options.get("broker_transport_options") or {}),
                "priority_steps": list(PRIORITY_STEPS),
            }
        return replace(
            base,
            broker_url=broker_url,
            result_backend=(
                self._settings.task_bridge_result_backend or base.result_backend
            ),
            # Safety net only: ``submit`` always names the queue explicitly.
            default_queue=QueueName.MEMORY_JOBS.value,
            app_options=app_options,
        )

    async def bridge(self) -> CeleryBridge:
        bridge = self._bridge
        if bridge is not None:
            return bridge
        async with self._lock:
            if self._bridge is None:
                configured = CeleryBridge(self._config())
                # Open the result-backend connections before the first request
                # pays for them.
                await configured.awarm_up(connections=4)
                self._bridge = configured
            return self._bridge

    async def submit(
        self,
        task_name: str,
        *,
        kwargs: Mapping[str, Any],
        queue: str = QueueName.MEMORY_JOBS.value,
        priority: int = TaskPriority.INTERACTIVE,
    ) -> str:
        """Publish one task to memory-jobs and return its task id.

        Deliberately generic: this adapter knows the transport, not the business
        tasks. Callers pass a name from ``task_bridge.mem_router.TaskName`` and
        the worker's parameter names as ``kwargs``. ``queue`` and ``priority``
        default to memory-jobs' queue at the interactive level — what a request
        someone is waiting on needs — and a caller with a different urgency or
        destination states it explicitly.
        """

        bridge = await self.bridge()
        return await bridge.asubmit(
            task_name,
            kwargs=dict(kwargs),
            queue=queue,
            priority=priority,
        )

    async def await_result(self, task_id: str, *, wait_seconds: float) -> TaskStatus:
        """Wait for the task's terminal state.

        ``raise_on_error`` stays off: a FAILURE task is a business outcome the
        caller reports as a failed generation, not a transport problem.
        """

        bridge = await self.bridge()
        return await bridge.await_result(task_id, timeout=wait_seconds)

    async def aclose(self) -> None:
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            await bridge.aclose()
