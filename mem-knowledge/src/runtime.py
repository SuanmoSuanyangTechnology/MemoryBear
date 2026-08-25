"""Process-level ownership of knowledge service infrastructure."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from .bootstrap import get_settings
from .config import KnowledgeSettings
from .db import DatabaseManager
from .infrastructure import (
    ElasticsearchManager,
    ModelRuntimeManager,
    RedisManager,
    StorageManager,
)

T = TypeVar("T")


class _WorkerAsyncBridge:
    """Own one lazy event loop thread for a prefork child process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready: threading.Event | None = None
        self._closed = False

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._closed:
                raise RuntimeError("worker async bridge is closed")
            if self._loop is not None:
                return self._loop

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run_loop() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            thread = threading.Thread(
                target=run_loop,
                name="knowledge-async-loop",
                daemon=True,
            )
            self._loop = loop
            self._thread = thread
            self._ready = ready
            thread.start()

        ready.wait()
        return loop

    def run(self, factory: Callable[[], Awaitable[T]]) -> T:
        loop = self._ensure_started()
        return asyncio.run_coroutine_threadsafe(factory(), loop).result()

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
            self._loop = None
            self._thread = None
            self._ready = None
            self._closed = True
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join()

    def reset_after_fork(self) -> None:
        """Discard inherited thread state without touching parent resources."""

        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._ready = None
        self._closed = False


class ProcessRuntime:
    """Own all lazy resources for exactly one operating-system process."""

    def __init__(self, settings: KnowledgeSettings):
        self.settings = settings
        self._pid = os.getpid()
        self._closed = False
        self._bridge = _WorkerAsyncBridge()
        self._vision_executor: ThreadPoolExecutor | None = None
        self._vision_executor_lock = threading.RLock()
        self._create_managers()

    def _create_managers(self) -> None:
        self.database = DatabaseManager(self.settings)
        self.redis = RedisManager(self.settings)
        self.elasticsearch = ElasticsearchManager(self.settings)
        self.storage = StorageManager(self.settings)
        self.model_runtime = ModelRuntimeManager(self.settings)

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def vision_executor_initialized(self) -> bool:
        return self._vision_executor is not None

    @property
    def vision_executor(self) -> ThreadPoolExecutor:
        with self._vision_executor_lock:
            if self._closed:
                raise RuntimeError("process runtime is closed")
            if self._vision_executor is None:
                self._vision_executor = ThreadPoolExecutor(
                    max_workers=self.settings.kb_vision_max_workers,
                    thread_name_prefix="knowledge-vision-",
                )
            return self._vision_executor

    def run_async(self, factory: Callable[[], Awaitable[T]]) -> T:
        if self._closed:
            raise RuntimeError("process runtime is closed")
        return self._bridge.run(factory)

    def reset_after_fork(self, pid: int | None = None) -> None:
        self.database.reset_after_fork()
        self.redis.reset_after_fork()
        self.elasticsearch.reset_after_fork()
        self.storage.reset_after_fork()
        self.model_runtime.reset_after_fork()
        self._bridge.reset_after_fork()
        self._vision_executor = None
        self._vision_executor_lock = threading.RLock()
        self._pid = pid if pid is not None else os.getpid()
        self._closed = False
        self._create_managers()

    async def _close_async_resources(self, errors: list[Exception]) -> None:
        for close in (
            self.model_runtime.aclose,
            self.storage.aclose,
            self.redis.aclose,
            self.elasticsearch.aclose,
            self.database.aclose_async,
        ):
            try:
                await close()
            except Exception as exc:
                errors.append(exc)

    def _close_sync_resources(self, errors: list[Exception]) -> None:
        for close in (
            self.redis.close_sync,
            self.elasticsearch.close_sync,
            self.database.close_sync,
        ):
            try:
                close()
            except Exception as exc:
                errors.append(exc)

    def _close_vision_executor(self, errors: list[Exception]) -> None:
        with self._vision_executor_lock:
            executor = self._vision_executor
            self._vision_executor = None
        if executor is None:
            return
        try:
            executor.shutdown(wait=True, cancel_futures=True)
        except Exception as exc:
            errors.append(exc)

    async def aclose(self) -> None:
        if self._closed:
            return
        errors: list[Exception] = []
        await self._close_async_resources(errors)
        await asyncio.to_thread(self._close_sync_resources, errors)
        await asyncio.to_thread(self._close_vision_executor, errors)
        await asyncio.to_thread(self._bridge.close)
        self._closed = True
        if errors:
            raise errors[0]

    def close_sync(self) -> None:
        if self._closed:
            return
        errors: list[Exception] = []
        try:
            self._bridge.run(lambda: self._close_async_resources(errors))
        except Exception as exc:
            errors.append(exc)
        self._close_sync_resources(errors)
        self._close_vision_executor(errors)
        try:
            self._bridge.close()
        except Exception as exc:
            errors.append(exc)
        self._closed = True
        if errors:
            raise errors[0]


_worker_runtime: ProcessRuntime | None = None
_worker_runtime_lock = threading.Lock()


def get_worker_runtime() -> ProcessRuntime:
    global _worker_runtime
    with _worker_runtime_lock:
        if _worker_runtime is None:
            _worker_runtime = ProcessRuntime(get_settings())
        return _worker_runtime


def reset_worker_runtime_after_fork() -> ProcessRuntime:
    global _worker_runtime
    with _worker_runtime_lock:
        if _worker_runtime is None:
            _worker_runtime = ProcessRuntime(get_settings())
        else:
            _worker_runtime.reset_after_fork()
        return _worker_runtime


async def shutdown_worker_runtime() -> None:
    global _worker_runtime
    with _worker_runtime_lock:
        runtime = _worker_runtime
        _worker_runtime = None
    if runtime is not None:
        await runtime.aclose()


def shutdown_worker_runtime_sync() -> None:
    global _worker_runtime
    with _worker_runtime_lock:
        runtime = _worker_runtime
        _worker_runtime = None
    if runtime is not None:
        runtime.close_sync()
