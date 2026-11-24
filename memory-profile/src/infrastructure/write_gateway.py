"""Publish API-worker tasks (reflection, reembed) and await results.

This client no longer carries any write or emotion-reply work: the write
endpoint was removed from this service and memory-jobs owns the write path; the
API keeps its own local emotion-reply implementation. Its callers are the
API-worker tasks the profile still delegates (reflection and the workspace
reembed retry).

No queue priority is declared: the API worker owns ``memory_tasks`` without
``x-max-priority``, unlike the memory-jobs queues the task bridge publishes to,
and declaring it here would trip RabbitMQ's PRECONDITION_FAILED.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from task_bridge import CeleryBridge, TaskStatus
from task_bridge.transports.celery.config import CeleryConfig

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

#: API-side task names (registered in api/app/tasks.py, routed to memory_tasks).
MERGE_TASK_NAME = "app.tasks.memory_merge_gateway"
STORAGE_DELETE_END_USER_TASK_NAME = (
    "app.tasks.memory_storage_end_user_delete_gateway"
)
STORAGE_NODE_DELETE_TASK_NAME = "app.tasks.memory_storage_node_delete_gateway"
REEMBED_RETRY_TASK_NAME = "app.tasks.memory_workspace_reembed_retry_gateway"
TOTAL_MEMORY_TASK_NAME = "app.tasks.memory_dashboard_total_memory_gateway"
REFLECTION_WORKSPACE_TASK_NAME = "app.tasks.memory_reflection_workspace_gateway"
REFLECTION_RUN_TASK_NAME = "app.tasks.memory_reflection_run_gateway"


class WriteGatewayError(RuntimeError):
    """The gateway task failed, timed out, or returned a malformed result."""


class WriteGatewayClient:
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
            broker_url=(
                self._settings.write_gateway_broker_url or base.broker_url
            ),
            result_backend=(
                self._settings.write_gateway_result_backend or base.result_backend
            ),
            default_queue=self._settings.write_gateway_queue,
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

    async def submit_api_task(
        self, task_name: str, kwargs: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Publish one API-worker task and wait for its plain result dict."""

        bridge = await self.bridge()
        task_id = await bridge.asubmit(
            task_name,
            kwargs=dict(kwargs),
            queue=self._settings.write_gateway_queue,
        )
        try:
            status = await bridge.await_result(
                task_id, timeout=self._settings.write_gateway_wait_seconds
            )
        except TimeoutError as exc:
            raise WriteGatewayError(
                f"api task {task_id} did not finish in "
                f"{self._settings.write_gateway_wait_seconds}s"
            ) from exc
        return _result_envelope(task_id, status)

    async def aclose(self) -> None:
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            await bridge.aclose()


def _result_envelope(task_id: str, status: TaskStatus) -> dict[str, Any]:
    if status.state != "SUCCESS" or not isinstance(status.result, Mapping):
        detail = status.traceback or status.result
        raise WriteGatewayError(
            f"write gateway task {task_id} ended {status.state}: {detail}"
        )
    return dict(status.result)


__all__ = [
    "MERGE_TASK_NAME",
    "REEMBED_RETRY_TASK_NAME",
    "REFLECTION_RUN_TASK_NAME",
    "REFLECTION_WORKSPACE_TASK_NAME",
    "STORAGE_DELETE_END_USER_TASK_NAME",
    "STORAGE_NODE_DELETE_TASK_NAME",
    "TOTAL_MEMORY_TASK_NAME",
    "WriteGatewayClient",
    "WriteGatewayError",
]





