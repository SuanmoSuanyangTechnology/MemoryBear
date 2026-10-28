"""Exceptions raised by the task bridge.

The hierarchy is deliberately small: configuration problems, publish problems,
result-read problems, and the two ordinary outcomes of waiting on a task.
Every error carries enough context to log it without repeating the call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_bridge.core.status import TaskStatus


class BridgeError(Exception):
    """Base class for every bridge error."""


class BridgeConfigurationError(BridgeError):
    """The bridge cannot work with the given configuration."""


class InvalidTaskPayloadError(BridgeError, ValueError):
    """The task payload cannot be carried by the JSON task protocol."""


class TaskPublishError(BridgeError):
    """The broker rejected or could not receive the task message."""


class TaskResultUnavailableError(BridgeError):
    """The task result could not be read from the result backend."""


class TaskFailedError(BridgeError):
    """A waited-on task finished in the ``FAILURE`` state."""

    def __init__(self, status: TaskStatus):
        self.status = status
        self.task_id = status.task_id
        super().__init__(
            f"task {status.task_id!r} failed: {status.error or 'no error detail'}"
        )


class TaskTimeoutError(BridgeError):
    """Waiting ended before the task reached a terminal state.

    ``status`` is the last state observed, so callers can still report
    ``PENDING`` (never picked up) versus ``STARTED`` (still running).
    """

    def __init__(
        self,
        task_id: str,
        timeout: float | None,
        status: TaskStatus | None = None,
    ):
        self.task_id = task_id
        self.timeout = timeout
        self.status = status
        last_state = status.state if status is not None else "UNKNOWN"
        super().__init__(
            f"timed out after {timeout}s waiting for task {task_id!r} "
            f"(last state: {last_state})"
        )


__all__ = [
    "BridgeConfigurationError",
    "BridgeError",
    "InvalidTaskPayloadError",
    "TaskFailedError",
    "TaskPublishError",
    "TaskResultUnavailableError",
    "TaskTimeoutError",
]
