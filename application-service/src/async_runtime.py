"""One long-lived asyncio loop per worker process.

Async resources are bound to the loop that created them: the Redis asyncio
client refuses cross-loop reuse, asyncpg connections and httpx AsyncClient
transports are loop-bound, and BatchPersistQueue owns an asyncio.Queue.
Celery prefork children run tasks on arbitrary threads, so every coroutine
must be scheduled onto one stable loop instead of asyncio.run per task.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

from .integrations.knowledge.runtime import (
    close_knowledge_integration,
    initialize_knowledge_integration,
)
from .integrations.sandbox.runtime import (
    close_sandbox_integration,
    initialize_sandbox_integration,
)
from .runtime import get_process_runtime
from .services.batch_persist_queue import BatchPersistQueue
from .workflow.nodes.http_client import close_http_client, init_http_client

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
AsyncHook = Callable[[], Awaitable[None]]


async def _service_startup() -> None:
    # Workflow runs resolve these process-level clients lazily (http_request /
    # knowledge / sandbox nodes and post-run message persistence).
    await init_http_client()
    await initialize_knowledge_integration()
    await initialize_sandbox_integration()
    await BatchPersistQueue.start()


async def _service_shutdown() -> None:
    await BatchPersistQueue.flush()
    await BatchPersistQueue.stop()
    await close_http_client()
    await close_knowledge_integration()
    await close_sandbox_integration()
    await get_process_runtime().aclose()


class AsyncRuntime:
    """Own one event loop on a dedicated daemon thread."""

    def __init__(
        self,
        *,
        startup: AsyncHook | None = _service_startup,
        shutdown: AsyncHook | None = _service_shutdown,
    ) -> None:
        self._startup = startup
        self._shutdown = shutdown
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("async runtime is not started")
        return self._loop

    def start(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="app-service-async", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=30):
            self._teardown()
            raise RuntimeError("async runtime loop did not start within 30s")
        if self._startup is not None:
            try:
                self.run(self._startup())
            except BaseException:
                self._teardown()
                raise

    def _teardown(self) -> None:
        loop, thread = self._loop, self._thread
        self._thread = None
        self._loop = None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None:
            thread.join(timeout=30)
            if thread.is_alive():
                logger.error("async runtime loop did not stop within 30s")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def run(self, coro: Coroutine[Any, Any, _T], timeout: float | None = None) -> _T:
        if self._loop is None:
            coro.close()
            raise RuntimeError("async runtime is not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            if self._shutdown is not None:
                self.run(self._shutdown())
        except Exception:
            logger.exception("async runtime shutdown hook failed")
        self._teardown()


_runtime: AsyncRuntime | None = None


def start_async_runtime() -> None:
    global _runtime
    if _runtime is None:
        _runtime = AsyncRuntime()
    try:
        _runtime.start()
    except BaseException:
        _runtime = None
        raise


def stop_async_runtime() -> None:
    global _runtime
    if _runtime is not None:
        _runtime.stop()
        _runtime = None


def run_async[T](coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
    """Run a coroutine on the process loop; call only from task threads."""
    if _runtime is None:
        coro.close()
        raise RuntimeError(
            "async runtime is not started; worker_process_init must run first"
        )
    return _runtime.run(coro, timeout)
