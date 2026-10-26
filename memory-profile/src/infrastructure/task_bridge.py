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
from uuid import UUID

from task_bridge import CeleryBridge, TaskStatus
from task_bridge.transports.celery.config import CeleryConfig

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

# Task names are the contract with the worker registry in ``memory-jobs``
# (``src/tasks/__init__.py``); renaming one here without renaming it there
# only makes submissions sit in the queue until the wait timeout. They are
# deliberately not settings: no deployment should point a producer at a
# different task.
GENERATE_CACHE_TASK_NAME = "memory_jobs.profile_cache.generate"
GENERATE_CACHE_WORKSPACE_TASK_NAME = "memory_jobs.profile_cache.generate_workspace"
INTEREST_DISTRIBUTION_TASK_NAME = "memory_jobs.interest_distribution.generate"

#: All three tasks route to the worker's "heavy" queue, matching
#: ``TASK_ROUTES`` in ``memory-jobs/src/tasks/__init__.py``.
MEMORY_HEAVY_QUEUE = "memory_heavy_tasks"


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
        """Env-derived broker/backend, with this service's task routing applied.

        ``from_env`` does the URL resolution (TASK_BRIDGE_* → CELERY_* → Redis
        DB 3/4) so producers and workers agree; only the task names and queue
        *this* service produces into are added here.
        """

        base = CeleryConfig.from_env()
        return replace(
            base,
            broker_url=self._settings.task_bridge_broker_url or base.broker_url,
            result_backend=(
                self._settings.task_bridge_result_backend or base.result_backend
            ),
            default_queue=MEMORY_HEAVY_QUEUE,
            task_routes={
                **base.task_routes,
                GENERATE_CACHE_TASK_NAME: MEMORY_HEAVY_QUEUE,
                GENERATE_CACHE_WORKSPACE_TASK_NAME: MEMORY_HEAVY_QUEUE,
                INTEREST_DISTRIBUTION_TASK_NAME: MEMORY_HEAVY_QUEUE,
            },
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

    async def _submit(self, task_name: str, kwargs: Mapping[str, Any], queue: str) -> str:
        bridge = await self.bridge()
        return await bridge.asubmit(task_name, kwargs=dict(kwargs), queue=queue)

    async def submit_generate_cache(
        self,
        *,
        end_user_id: str,
        workspace_id: UUID,
        language: str,
    ) -> str:
        """Publish one cache-generation unit and return its task id."""

        return await self._submit(
            GENERATE_CACHE_TASK_NAME,
            {
                "end_user_id": end_user_id,
                "workspace_id": str(workspace_id),
                "language": language,
            },
            MEMORY_HEAVY_QUEUE,
        )

    async def submit_generate_cache_workspace(
        self,
        *,
        workspace_id: UUID,
        language: str,
    ) -> str:
        """Publish the whole-workspace batch and return its task id."""

        return await self._submit(
            GENERATE_CACHE_WORKSPACE_TASK_NAME,
            {
                "workspace_id": str(workspace_id),
                "language": language,
            },
            MEMORY_HEAVY_QUEUE,
        )

    async def submit_interest_distribution(
        self,
        *,
        end_user_id: str,
        workspace_id: UUID,
        language: str,
        limit: int,
    ) -> str:
        """Publish one interest-distribution unit and return its task id."""

        return await self._submit(
            INTEREST_DISTRIBUTION_TASK_NAME,
            {
                "end_user_id": end_user_id,
                "workspace_id": str(workspace_id),
                "language": language,
                "limit": limit,
            },
            MEMORY_HEAVY_QUEUE,
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
