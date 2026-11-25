"""Celery adapter for the unit-publisher port.

The adapter sends by task name through ``current_app`` — the app the worker was
started with — so infrastructure never imports the interface layer's task
modules (nor their registration side effects). Routing, priority and countdown
reach Celery exactly as they would through ``apply_async``.
"""

from __future__ import annotations

from typing import Any, Protocol

from celery import current_app
from task_bridge.mem_router import TaskName, TaskPriority

from src.application.ports.publishing import Priority
from src.infrastructure.stagger import stagger_delay

#: The application's semantic levels mapped onto the shared broker contract.
_TRANSPORT_PRIORITY = {
    Priority.INTERACTIVE: int(TaskPriority.INTERACTIVE),
    Priority.NORMAL: int(TaskPriority.NORMAL),
    Priority.BACKGROUND: int(TaskPriority.BACKGROUND),
}


class TaskSender(Protocol):
    """The slice of the Celery app this adapter uses."""

    def send_task(self, name: str, **options: Any) -> Any: ...


class CeleryUnitPublisher:
    def __init__(self, app: TaskSender | None = None) -> None:
        self._app = app

    def _sender(self) -> TaskSender:
        # ``current_app`` is a proxy, not a snapshot: resolve it at send time so
        # the publisher binds to the app the process configured, no matter when
        # the container happened to be built.
        return self._app if self._app is not None else current_app

    def publish_profile_cache(
        self,
        *,
        end_user_id: str,
        workspace_id: str,
        language: str,
        refresh_insight: bool,
        refresh_summary: bool,
        priority: Priority,
        dispatch_index: int,
    ) -> None:
        self._sender().send_task(
            TaskName.PROFILE_CACHE_GENERATE.value,
            kwargs={
                "end_user_id": end_user_id,
                "workspace_id": workspace_id,
                "language": language,
                "refresh_insight": refresh_insight,
                "refresh_summary": refresh_summary,
            },
            countdown=stagger_delay(dispatch_index),
            priority=_TRANSPORT_PRIORITY[priority],
        )

    def publish_interest_distribution(
        self,
        *,
        end_user_id: str,
        workspace_id: str,
        language: str,
        limit: int,
        write_cache: bool,
        priority: Priority,
        dispatch_index: int,
    ) -> None:
        self._sender().send_task(
            TaskName.INTEREST_DISTRIBUTION_GENERATE.value,
            kwargs={
                "end_user_id": end_user_id,
                "workspace_id": workspace_id,
                "language": language,
                "limit": limit,
                "write_cache": write_cache,
            },
            countdown=stagger_delay(dispatch_index),
            priority=_TRANSPORT_PRIORITY[priority],
        )
