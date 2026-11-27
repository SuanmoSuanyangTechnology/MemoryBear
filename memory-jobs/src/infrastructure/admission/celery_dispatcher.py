"""Celery adapter for the admission ``JobDispatcher`` port.

Publishing by task name through ``current_app`` keeps infrastructure from
importing the interface layer, exactly like the unit publisher. Two rules are
worth spelling out:

* the admitted message's params are passed through untouched except for the
  extra ``ADMISSION_MSG_ID_KEY`` kwarg, which is how the work task later reports
  its completion back into the admission layer;
* an admission task name is never dispatched as admitted work — that would put
  a dispatch unit on the admission queue twice and cannot make progress.
"""

from __future__ import annotations

from typing import Any, Protocol

from celery import current_app
from task_bridge.mem_router import TASK_ROUTES, QueueName, TaskName, TaskPriority

from src.application.ports.admission.admission import (
    ADMISSION_MSG_ID_KEY,
    QueuedUnitMessage,
)

#: Queue owned by the admission worker; never a destination for admitted work.
_ADMISSION_QUEUE = QueueName.MEMORY_ADMISSION.value
_ADMISSION_TASKS = frozenset(
    {TaskName.ADMISSION_DISPATCH.value, TaskName.ADMISSION_RECOVER.value}
)


class TaskSender(Protocol):
    """The slice of the Celery app this adapter uses."""

    def send_task(self, name: str, **options: Any) -> Any: ...


class CeleryJobDispatcher:
    def __init__(self, app: TaskSender | None = None) -> None:
        self._app = app

    def _sender(self) -> TaskSender:
        # ``current_app`` is a proxy, not a snapshot: resolve it at send time so
        # the dispatcher binds to the app the process configured.
        return self._app if self._app is not None else current_app

    def dispatch(self, message: QueuedUnitMessage) -> str:
        # An admitted message may name its own queue: the API's write tasks run
        # on the API's workers, so this service's routing table cannot know them.
        queue = (
            message.queue
            or TASK_ROUTES.get(message.task_name, QueueName.MEMORY_JOBS.value)
        )
        if message.task_name in _ADMISSION_TASKS or queue == _ADMISSION_QUEUE:
            raise ValueError(
                "refusing to dispatch admission task "
                f"{message.task_name!r} as admitted work"
            )
        kwargs = dict(message.params)
        if not message.queue:
            # Only this service's tasks declare the completion hook. A message
            # that names its own queue belongs to another service (the API's
            # write tasks): its handler has a fixed signature, so the extra
            # keyword would make it raise before doing any work, and it could
            # not report completion anyway. Such an attempt is finalized from
            # the result backend by the recovery sweep instead.
            kwargs[ADMISSION_MSG_ID_KEY] = message.msg_id
        result = self._sender().send_task(
            message.task_name,
            kwargs=kwargs,
            queue=queue,
            priority=int(message.priority),
        )
        task_id = getattr(result, "id", None)
        if not task_id:
            raise RuntimeError(
                f"the broker returned no task id for {message.task_name!r}"
            )
        return str(task_id)

    def wake(self, unit_key: str) -> None:
        self._sender().send_task(
            TaskName.ADMISSION_DISPATCH.value,
            kwargs={"unit_key": unit_key},
            queue=_ADMISSION_QUEUE,
            priority=int(TaskPriority.NORMAL),
        )


__all__ = ["CeleryJobDispatcher"]