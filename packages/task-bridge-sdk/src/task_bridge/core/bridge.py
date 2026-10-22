"""Transport-neutral task bridge facade.

:class:`TaskBridge` owns the parts that are the same for every dispatch
mechanism: argument shape checks, building a
:class:`~task_bridge.core.transport.TaskSubmission`, polling with timeout and
jitter, and the sync / async API. The mechanism itself lives in a
:class:`~task_bridge.core.transport.TaskTransport`.

Concrete assemblers live under ``task_bridge.transports`` (Celery today).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from task_bridge.core.errors import (
    BridgeConfigurationError,
    InvalidTaskPayloadError,
    TaskFailedError,
    TaskTimeoutError,
)
from task_bridge.core.status import TaskStatus
from task_bridge.core.transport import TaskSubmission, TaskTransport

_DEFAULT_POLL_INTERVAL = 0.5


class TaskBridge:
    """Mechanism-neutral task submission and status API.

    ``poll_jitter`` spreads out pollers that started together: each sleep is
    ``interval * (1 + uniform(0, jitter))``.
    """

    def __init__(self, transport: TaskTransport, *, poll_jitter: float = 0.1) -> None:
        if not isinstance(transport, TaskTransport):
            raise TypeError("transport must be a TaskTransport")
        if not 0.0 <= poll_jitter <= 1.0:
            raise BridgeConfigurationError("poll_jitter must be between 0 and 1")
        self._transport = transport
        self._poll_jitter = poll_jitter

    @property
    def transport(self) -> TaskTransport:
        return self._transport

    # ── Publishing ──────────────────────────────────────────────────────

    def submit(
        self,
        task_name: str,
        *,
        args: Sequence[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        queue: str | None = None,
        task_id: str | None = None,
        countdown: float | None = None,
        eta: datetime | None = None,
        expires: datetime | float | timedelta | None = None,
        priority: int | None = None,
        time_limit: float | None = None,
        soft_time_limit: float | None = None,
        headers: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> str:
        """Publish a task and return its id.

        The named arguments are common across mechanisms; transport-specific
        knobs (Celery's ``shadow`` / ``reply_to`` / ``link`` ...) go through
        ``**options``.
        """

        submission = self._build_submission(
            task_name,
            args=args,
            kwargs=kwargs,
            queue=queue,
            task_id=task_id,
            countdown=countdown,
            eta=eta,
            expires=expires,
            priority=priority,
            time_limit=time_limit,
            soft_time_limit=soft_time_limit,
            headers=headers,
            options=options,
        )
        return self._transport.submit(submission)

    async def asubmit(
        self,
        task_name: str,
        *,
        args: Sequence[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        queue: str | None = None,
        task_id: str | None = None,
        countdown: float | None = None,
        eta: datetime | None = None,
        expires: datetime | float | timedelta | None = None,
        priority: int | None = None,
        time_limit: float | None = None,
        soft_time_limit: float | None = None,
        headers: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> str:
        """:meth:`submit` without blocking the event loop."""

        submission = self._build_submission(
            task_name,
            args=args,
            kwargs=kwargs,
            queue=queue,
            task_id=task_id,
            countdown=countdown,
            eta=eta,
            expires=expires,
            priority=priority,
            time_limit=time_limit,
            soft_time_limit=soft_time_limit,
            headers=headers,
            options=options,
        )
        return await self._transport.asubmit(submission)

    # ── Status ──────────────────────────────────────────────────────────

    def get_status(self, task_id: str) -> TaskStatus:
        """Read one status snapshot.

        Whether an unknown id is distinguishable from a pending task depends
        on the transport; Celery reports both as ``PENDING``.
        """

        return self._transport.get_status(self._normalize_task_id(task_id))

    async def aget_status(self, task_id: str) -> TaskStatus:
        """:meth:`get_status` without blocking the event loop."""

        return await self._transport.aget_status(self._normalize_task_id(task_id))

    def get_status_many(self, task_ids: Sequence[str]) -> dict[str, TaskStatus]:
        """Read several statuses; duplicate ids are collapsed."""

        ids = self._normalize_task_ids(task_ids)
        if not ids:
            return {}
        return self._transport.get_status_many(ids)

    async def aget_status_many(self, task_ids: Sequence[str]) -> dict[str, TaskStatus]:
        """:meth:`get_status_many` without blocking the event loop."""

        ids = self._normalize_task_ids(task_ids)
        if not ids:
            return {}
        return await self._transport.aget_status_many(ids)

    def wait_for_result(
        self,
        task_id: str,
        *,
        timeout: float | None = None,
        interval: float = _DEFAULT_POLL_INTERVAL,
        raise_on_error: bool = False,
    ) -> TaskStatus:
        """Block until the task reaches a terminal state."""

        task_id = self._normalize_task_id(task_id)
        deadline = self._deadline(timeout)
        interval = self._check_interval(interval)
        while True:
            status = self._transport.get_status(task_id)
            if status.terminal:
                return _settle(status, raise_on_error)
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TaskTimeoutError(task_id, timeout, status)
            delay = self._next_delay(interval)
            time.sleep(delay if remaining is None else min(delay, remaining))

    async def await_result(
        self,
        task_id: str,
        *,
        timeout: float | None = None,
        interval: float = _DEFAULT_POLL_INTERVAL,
        raise_on_error: bool = False,
    ) -> TaskStatus:
        """Wait for a terminal state without blocking the event loop.

        When the transport offers push notifications, the wait is woken as
        soon as the state changes; ``interval`` stays as the fallback and the
        timeout granularity.
        """

        task_id = self._normalize_task_id(task_id)
        deadline = self._deadline(timeout)
        interval = self._check_interval(interval)

        watch = await self._transport.watch(task_id)
        try:
            while True:
                if watch is not None:
                    # Clear before reading: a state written from here on
                    # either sets the signal or is seen by the read below.
                    watch.clear()
                status = await self._transport.aget_status(task_id)
                if status.terminal:
                    return _settle(status, raise_on_error)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TaskTimeoutError(task_id, timeout, status)
                delay = self._next_delay(interval)
                wait_for = delay if remaining is None else min(delay, remaining)
                if watch is None:
                    await asyncio.sleep(wait_for)
                else:
                    await watch.wait(wait_for)
        finally:
            if watch is not None:
                await watch.aclose()

    # ── Control / lifecycle ─────────────────────────────────────────────

    def revoke(
        self, task_id: str, *, terminate: bool = False, signal: str | None = None
    ) -> None:
        """Best-effort cancellation; transports may not support it."""

        self._transport.revoke(
            self._normalize_task_id(task_id), terminate=terminate, signal=signal
        )

    async def arevoke(
        self, task_id: str, *, terminate: bool = False, signal: str | None = None
    ) -> None:
        """:meth:`revoke` without blocking the event loop."""

        await self._transport.arevoke(
            self._normalize_task_id(task_id), terminate=terminate, signal=signal
        )

    def warm_up(self, *, connections: int = 1) -> None:
        """Open result-backend connections ahead of traffic."""

        self._transport.warm_up(connections=connections)

    async def awarm_up(self, *, connections: int = 1) -> None:
        """:meth:`warm_up` without blocking the event loop."""

        await self._transport.awarm_up(connections=connections)

    def close(self) -> None:
        """Release the transport's resources."""

        self._transport.close()

    async def aclose(self) -> None:
        """:meth:`close` without blocking the event loop."""

        await self._transport.aclose()

    def __enter__(self) -> TaskBridge:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    async def __aenter__(self) -> TaskBridge:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ── Helpers ─────────────────────────────────────────────────────────

    def _build_submission(
        self,
        task_name: str,
        *,
        args: Sequence[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        queue: str | None = None,
        task_id: str | None = None,
        countdown: float | None = None,
        eta: datetime | None = None,
        expires: datetime | float | timedelta | None = None,
        priority: int | None = None,
        time_limit: float | None = None,
        soft_time_limit: float | None = None,
        headers: Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> TaskSubmission:
        if not task_name or not str(task_name).strip():
            raise InvalidTaskPayloadError("task_name must be a non-empty string")

        task_options: dict[str, Any] = dict(options or {})
        for key, value in (
            ("countdown", countdown),
            ("eta", eta),
            ("expires", expires),
            ("priority", priority),
            ("time_limit", time_limit),
            ("soft_time_limit", soft_time_limit),
        ):
            if value is not None:
                task_options[key] = value

        return TaskSubmission(
            task_name=str(task_name),
            args=self._normalize_args(args),
            kwargs=self._normalize_kwargs(kwargs),
            task_id=None if task_id is None else str(task_id),
            queue=queue,
            headers=None if headers is None else dict(headers),
            options=task_options,
        )

    @staticmethod
    def _normalize_args(args: Sequence[Any] | None) -> tuple[Any, ...]:
        if args is None:
            return ()
        if isinstance(args, (str, bytes, bytearray)):
            raise InvalidTaskPayloadError(
                "args must be a sequence of task arguments, not a string"
            )
        if isinstance(args, Mapping):
            raise InvalidTaskPayloadError(
                "args must be a sequence of task arguments; pass a mapping as kwargs"
            )
        try:
            return tuple(args)
        except TypeError as exc:
            raise InvalidTaskPayloadError(
                "args must be a sequence of task arguments"
            ) from exc

    @staticmethod
    def _normalize_kwargs(kwargs: Mapping[str, Any] | None) -> dict[str, Any]:
        if kwargs is None:
            return {}
        if not isinstance(kwargs, Mapping):
            raise InvalidTaskPayloadError("kwargs must be a mapping of task keyword arguments")
        return dict(kwargs)

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        if not task_id or not str(task_id).strip():
            raise ValueError("task_id must be a non-empty string")
        return str(task_id)

    @classmethod
    def _normalize_task_ids(cls, task_ids: Sequence[str]) -> list[str]:
        return list(dict.fromkeys(cls._normalize_task_id(task_id) for task_id in task_ids))

    @staticmethod
    def _check_interval(interval: float) -> float:
        if interval <= 0:
            raise ValueError("interval must be greater than zero")
        return interval

    def _next_delay(self, interval: float) -> float:
        """Spread polls out instead of letting every waiter hit on one tick."""

        return interval * (1.0 + random.uniform(0.0, self._poll_jitter))

    @staticmethod
    def _deadline(timeout: float | None) -> float | None:
        if timeout is None:
            return None
        if timeout < 0:
            raise ValueError("timeout must be zero or greater")
        return time.monotonic() + timeout


def _settle(status: TaskStatus, raise_on_error: bool) -> TaskStatus:
    if raise_on_error and status.failed:
        raise TaskFailedError(status)
    return status


__all__ = ["TaskBridge"]
