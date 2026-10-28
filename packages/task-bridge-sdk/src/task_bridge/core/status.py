"""Task states and the immutable status snapshot returned to callers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    """Celery's built-in states, plus the ones a bridge caller cares about.

    Celery allows worker code to publish custom states (``PROGRESS`` is the
    common one). ``TaskStatus.state`` therefore stays a plain string; these
    constants exist so callers can compare without importing Celery.
    """

    PENDING = "PENDING"
    RECEIVED = "RECEIVED"
    STARTED = "STARTED"
    RETRY = "RETRY"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"
    IGNORED = "IGNORED"
    UNKNOWN = "UNKNOWN"


#: States for which Celery considers the result final (``AsyncResult.ready()``).
READY_STATES: frozenset[str] = frozenset(
    {TaskState.SUCCESS, TaskState.FAILURE, TaskState.REVOKED}
)

#: States that will not change again, including the result-less ``IGNORED``.
TERMINAL_STATES: frozenset[str] = READY_STATES | {TaskState.IGNORED}


@dataclass(frozen=True, slots=True)
class TaskStatus:
    """One snapshot of a task's state.

    ``result`` is the task's return value once the state is ``SUCCESS``. For
    non-terminal states it is whatever the backend stored, which is normally
    ``None`` and may hold metadata when the task reports progress. ``meta``
    keeps the raw backend payload for fields the bridge does not interpret
    (worker name, ``date_done``, custom progress dictionaries).
    """

    task_id: str
    state: str
    result: Any = None
    exception: BaseException | None = None
    traceback: str | None = None
    meta: Mapping[str, Any] | None = None

    @classmethod
    def from_meta(cls, task_id: str, meta: Mapping[str, Any] | None) -> TaskStatus:
        """Build a snapshot from a decoded Celery result-backend payload."""

        payload = dict(meta or {})
        state = str(payload.get("status") or TaskState.PENDING)
        value = payload.get("result")

        exception: BaseException | None = None
        result: Any = value
        if state == TaskState.FAILURE:
            if isinstance(value, BaseException):
                exception = value
                result = None
            # A string/dict failure payload stays in ``result``; ``error``
            # exposes it as text.

        traceback = payload.get("traceback")
        return cls(
            task_id=task_id,
            state=state,
            result=result,
            exception=exception,
            traceback=str(traceback) if traceback else None,
            meta=payload,
        )

    @property
    def ready(self) -> bool:
        """Whether Celery considers the result final."""

        return self.state in READY_STATES

    @property
    def terminal(self) -> bool:
        """Whether waiting should stop, including ``IGNORED`` tasks."""

        return self.state in TERMINAL_STATES

    @property
    def successful(self) -> bool:
        return self.state == TaskState.SUCCESS

    @property
    def failed(self) -> bool:
        return self.state == TaskState.FAILURE

    @property
    def revoked(self) -> bool:
        return self.state == TaskState.REVOKED

    @property
    def pending(self) -> bool:
        return self.state == TaskState.PENDING

    @property
    def error(self) -> str | None:
        """Human-readable failure text, or ``None`` when not failed."""

        if self.exception is not None:
            text = str(self.exception)
            name = type(self.exception).__name__
            return f"{name}: {text}" if text else name
        if self.failed and self.result is not None:
            return str(self.result) or None
        return None


__all__ = ["READY_STATES", "TERMINAL_STATES", "TaskState", "TaskStatus"]
