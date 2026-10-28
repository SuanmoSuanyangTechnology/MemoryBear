"""Transport abstraction for task submission and result lookup.

A transport implements exactly one dispatch mechanism (Celery today; an HTTP
task service, Redis Streams, an in-memory fake in tests, ... tomorrow). The
:class:`~task_bridge.core.bridge.TaskBridge` facade owns the mechanism-agnostic
parts: payload shape checks, polling with timeout and jitter, and the sync /
async API.

Transports implement blocking primitives. The base class runs them in a
worker thread for the ``a*`` methods, so a sync-only transport works behind
the async API; a natively async transport overrides those methods. A
transport may also expose :meth:`TaskTransport.watch` to wake a waiter as
soon as a result changes instead of waiting for the next poll.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from task_bridge.core.status import TaskStatus


@dataclass(frozen=True, slots=True)
class TaskSubmission:
    """A transport-neutral task submission.

    ``options`` carries mechanism-specific knobs (for Celery: ``countdown``,
    ``eta``, ``time_limit``, ``shadow`` and anything else ``send_task``
    accepts). A transport ignores or rejects options it does not understand.
    """

    task_name: str
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    queue: str | None = None
    headers: Mapping[str, Any] | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


class TaskWatch(ABC):
    """Optional push notification for one task's state changes.

    ``clear()`` must be called before reading the task's status: a signal
    that arrives after the clear either leaves ``wait()`` ready or is visible
    to the status read, so a terminal state is never missed.
    """

    @abstractmethod
    def clear(self) -> None:
        """Drop any pending signal."""

    @abstractmethod
    async def wait(self, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for the next signal; timeout is not an error."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release the subscription."""


class TaskTransport(ABC):
    """Blocking primitives for one dispatch mechanism."""

    @abstractmethod
    def submit(self, submission: TaskSubmission) -> str:
        """Publish a task and return its id."""

    @abstractmethod
    def get_status(self, task_id: str) -> TaskStatus:
        """Read one status snapshot."""

    def get_status_many(self, task_ids: Sequence[str]) -> dict[str, TaskStatus]:
        """Read several statuses; override when the backend can batch."""

        return {task_id: self.get_status(task_id) for task_id in task_ids}

    def revoke(
        self, task_id: str, *, terminate: bool = False, signal: str | None = None
    ) -> None:
        """Best-effort cancellation; not every mechanism supports it."""

        raise NotImplementedError(f"{type(self).__name__} does not support revoke")

    def warm_up(self, *, connections: int = 1) -> None:
        """Open result-backend connections; default is a no-op."""

    def close(self) -> None:
        """Release resources; default is a no-op."""

    async def _run_blocking(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(func, *args, **kwargs)

    async def asubmit(self, submission: TaskSubmission) -> str:
        return await self._run_blocking(self.submit, submission)

    async def aget_status(self, task_id: str) -> TaskStatus:
        return await self._run_blocking(self.get_status, task_id)

    async def aget_status_many(
        self, task_ids: Sequence[str]
    ) -> dict[str, TaskStatus]:
        return await self._run_blocking(self.get_status_many, task_ids)

    async def arevoke(
        self, task_id: str, *, terminate: bool = False, signal: str | None = None
    ) -> None:
        await self._run_blocking(self.revoke, task_id, terminate=terminate, signal=signal)

    async def awarm_up(self, *, connections: int = 1) -> None:
        await self._run_blocking(self.warm_up, connections=connections)

    async def aclose(self) -> None:
        await self._run_blocking(self.close)

    async def watch(self, task_id: str) -> TaskWatch | None:
        """Return a push-notification handle, or ``None`` to poll."""

        return None


class ThreadedTaskTransport(TaskTransport):
    """Transport whose blocking calls may use a dedicated executor.

    ``executor`` is injected (and never shut down by the transport);
    ``executor_workers`` creates an owned pool that ``close()`` shuts down.
    """

    def __init__(
        self,
        *,
        executor: ThreadPoolExecutor | None = None,
        executor_workers: int | None = None,
    ) -> None:
        if executor is not None and executor_workers is not None:
            raise ValueError("pass either executor or executor_workers, not both")
        self._executor = executor
        self._owns_executor = False
        self._executor_workers = executor_workers
        self._executor_lock = threading.Lock()

    def _get_executor(self) -> ThreadPoolExecutor | None:
        executor = self._executor
        if executor is not None or self._executor_workers is None:
            return executor
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._executor_workers,
                    thread_name_prefix="task-bridge",
                )
                self._owns_executor = True
            return self._executor

    async def _run_blocking(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        executor = self._get_executor()
        if executor is None:
            return await asyncio.to_thread(func, *args, **kwargs)
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        call = functools.partial(context.run, func, *args, **kwargs)
        return await loop.run_in_executor(executor, call)

    def close(self) -> None:
        executor: ThreadPoolExecutor | None = None
        with self._executor_lock:
            if self._owns_executor and self._executor is not None:
                executor, self._executor = self._executor, None
                self._owns_executor = False
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=False)


__all__ = [
    "TaskSubmission",
    "TaskTransport",
    "TaskWatch",
    "ThreadedTaskTransport",
]
