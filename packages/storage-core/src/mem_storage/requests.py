"""Bound SDK operations through response cleanup, including after cancellation."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class StorageOperation:
    def __init__(self, file_key: str | None = None) -> None:
        self.file_key = file_key
        self.action = ""
        self.error_observed = False
        self.pending: asyncio.Task[Any] | None = None
        self.cleanup: Callable[[], None] | None = None

    async def call(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        self.action = function.__qualname__
        self.error_observed = False
        self.pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        # 取消调用者不能取消仍在运行的 SDK 线程，清理由 operation 的所有者接管。
        try:
            return await asyncio.shield(self.pending)
        except Exception:
            self.error_observed = True
            raise


class StorageRequests:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._closing = False
        self._condition = threading.Condition()
        self._waiters: deque[Future[None]] = deque()
        self._cleanups: set[asyncio.Task[None]] = set()

    async def _acquire(self) -> None:
        # API 事件循环和 Worker bridge 都可能访问同一实例，额度不能绑定到某个 loop。
        with self._condition:
            if self._closing:
                raise RuntimeError("storage backend is closing")
            if self._active < self._limit:
                self._active += 1
                return
            waiter: Future[None] = Future()
            self._waiters.append(waiter)
        try:
            await asyncio.shield(asyncio.wrap_future(waiter))
        except asyncio.CancelledError:
            with self._condition:
                cancelled_while_waiting = waiter.cancel()
                if cancelled_while_waiting:
                    self._waiters.remove(waiter)
            if not cancelled_while_waiting:
                self._release()
            raise

    def _release(self) -> None:
        with self._condition:
            if self._waiters:
                waiter = self._waiters.popleft()
                waiter.set_result(None)
            else:
                self._active -= 1
            self._condition.notify_all()

    @asynccontextmanager
    async def operation(self, file_key: str | None = None) -> AsyncIterator[StorageOperation]:
        await self._acquire()
        operation = StorageOperation(file_key)
        cancelled = False
        try:
            yield operation
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            cleanup = asyncio.create_task(self._finish(operation))
            with self._condition:
                self._cleanups.add(cleanup)
            cleanup.add_done_callback(self._cleanup_done)
            if not cancelled:
                await asyncio.shield(cleanup)

    async def _finish(self, operation: StorageOperation) -> None:
        try:
            if operation.pending is not None:
                try:
                    await asyncio.shield(operation.pending)
                except Exception:
                    if not operation.error_observed:
                        logger.exception(
                            "取消后的存储请求失败 stage=sdk_completion action=%s file_key=%s",
                            operation.action,
                            operation.file_key,
                        )
            if operation.cleanup is not None:
                await asyncio.to_thread(operation.cleanup)
        except Exception:
            logger.exception(
                "存储资源清理失败 stage=response_cleanup action=%s file_key=%s",
                operation.action,
                operation.file_key,
            )
        finally:
            self._release()

    def _cleanup_done(self, task: asyncio.Task[None]) -> None:
        with self._condition:
            self._cleanups.discard(task)

    async def call(
        self,
        function: Callable[..., T],
        *args: Any,
        storage_key: str | None = None,
        **kwargs: Any,
    ) -> T:
        async with self.operation(storage_key) as operation:
            return await operation.call(function, *args, **kwargs)

    def drain(self) -> None:
        """Stop admission and wait for operations and their cleanup off the event loop."""
        with self._condition:
            self._closing = True
            while self._active:
                self._condition.wait()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.drain)
