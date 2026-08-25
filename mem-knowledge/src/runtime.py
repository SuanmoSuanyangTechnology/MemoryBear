"""Process-level ownership of knowledge service infrastructure."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
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


class _RuntimeRun:
    """Identify one run accepted by a specific process runtime."""

    __slots__ = ("runtime",)

    def __init__(self, runtime: ProcessRuntime) -> None:
        self.runtime = runtime


_CURRENT_RUNTIME_RUN: ContextVar[_RuntimeRun | None] = ContextVar(
    "knowledge_current_runtime_run",
    default=None,
)


class _WorkerAsyncBridge:
    """Own one lazy event loop thread for a prefork child process."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._state = "open"
        self._active_submissions = 0

    def _ensure_started_locked(
        self,
    ) -> tuple[asyncio.AbstractEventLoop, threading.Event]:
        if self._loop is not None:
            ready = threading.Event()
            ready.set()
            return self._loop, ready

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
        try:
            self._loop = loop
            self._thread = thread
            thread.start()
        except Exception:
            self._loop = None
            self._thread = None
            loop.close()
            raise
        return loop, ready

    @property
    def started(self) -> bool:
        with self._condition:
            return self._loop is not None

    def is_current_thread(self) -> bool:
        with self._condition:
            return self._thread is threading.current_thread()

    def start_closing(self) -> None:
        with self._condition:
            if self._state == "open":
                self._state = "closing"
            self._condition.notify_all()

    def _submit(
        self,
        factory: Callable[[], Awaitable[T]],
        *,
        allow_closing: bool,
    ) -> T:
        with self._condition:
            if self._state == "closed" or (
                self._state == "closing" and not allow_closing
            ):
                raise RuntimeError("worker async bridge is closing or closed")
            if allow_closing:
                if self._thread is threading.current_thread():
                    raise RuntimeError("cannot synchronously submit from bridge thread")
                while self._active_submissions:
                    self._condition.wait()
            loop, ready = self._ensure_started_locked()
            self._active_submissions += 1

        ready.wait()
        try:
            return asyncio.run_coroutine_threadsafe(factory(), loop).result()
        finally:
            with self._condition:
                self._active_submissions -= 1
                self._condition.notify_all()

    def run(self, factory: Callable[[], Awaitable[T]]) -> T:
        return self._submit(factory, allow_closing=False)

    def run_accepted(self, factory: Callable[[], Awaitable[T]]) -> T:
        return self._submit(factory, allow_closing=True)

    def run_closing(self, factory: Callable[[], Awaitable[T]]) -> T:
        return self._submit(factory, allow_closing=True)

    def close(self) -> None:
        with self._condition:
            if self._thread is threading.current_thread():
                raise RuntimeError("worker async bridge cannot join its own thread")
            if self._state == "closed":
                return
            self._state = "closing"
            while self._active_submissions:
                self._condition.wait()
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            with self._condition:
                self._state = "closed"
                self._condition.notify_all()
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        with self._condition:
            self._loop = None
            self._thread = None
            self._state = "closed"
            self._condition.notify_all()

    def reset_after_fork(self) -> None:
        """Discard inherited thread state without touching parent resources."""

        self._condition = threading.Condition(threading.RLock())
        self._loop = None
        self._thread = None
        self._state = "open"
        self._active_submissions = 0


class ProcessRuntime:
    """Own all lazy resources for exactly one operating-system process."""

    def __init__(self, settings: KnowledgeSettings):
        self.settings = settings
        self._pid = os.getpid()
        self._bridge = _WorkerAsyncBridge()
        self._lifecycle = threading.Condition(threading.RLock())
        self._lifecycle_state = "open"
        self._active_runs = 0
        self._active_run_tokens: set[_RuntimeRun] = set()
        self._run_lock = threading.Lock()
        self._deferred_close_thread: threading.Thread | None = None
        self._close_errors: list[Exception] = []
        self._close_initiator_token: _RuntimeRun | None = None
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
        with self._lifecycle:
            return self._lifecycle_state == "closed"

    @property
    def vision_executor_initialized(self) -> bool:
        with self._vision_executor_lock:
            return self._vision_executor is not None

    @property
    def vision_executor(self) -> ThreadPoolExecutor:
        with self._lifecycle:
            if self._lifecycle_state != "open":
                raise RuntimeError("process runtime is closing or closed")
            with self._vision_executor_lock:
                if self._vision_executor is None:
                    self._vision_executor = ThreadPoolExecutor(
                        max_workers=self.settings.kb_vision_max_workers,
                        thread_name_prefix="knowledge-vision-",
                    )
                return self._vision_executor

    def run_async(self, factory: Callable[[], Awaitable[T]]) -> T:
        run_token = _RuntimeRun(self)

        async def run_with_context() -> T:
            context_token = _CURRENT_RUNTIME_RUN.set(run_token)
            try:
                return await factory()
            finally:
                _CURRENT_RUNTIME_RUN.reset(context_token)

        with self._lifecycle:
            if self._lifecycle_state != "open":
                raise RuntimeError("process runtime is closing or closed")
            self._active_runs += 1
            self._active_run_tokens.add(run_token)
        run_error: BaseException | None = None
        try:
            with self._run_lock:
                result = self._bridge.run_accepted(run_with_context)
        except BaseException as exc:
            run_error = exc
        finally:
            deferred_close_thread: threading.Thread | None = None
            with self._lifecycle:
                self._active_run_tokens.discard(run_token)
                self._active_runs -= 1
                if self._close_initiator_token is run_token:
                    deferred_close_thread = self._deferred_close_thread
                self._lifecycle.notify_all()
            if deferred_close_thread is not None:
                deferred_close_thread.join()
        if run_error is not None:
            raise run_error
        if deferred_close_thread is not None:
            self._raise_close_error()
        return result

    def reset_after_fork(self, pid: int | None = None) -> None:
        self.database.reset_after_fork()
        self.redis.reset_after_fork()
        self.elasticsearch.reset_after_fork()
        self.storage.reset_after_fork()
        self.model_runtime.reset_after_fork()
        self._bridge.reset_after_fork()
        self._lifecycle = threading.Condition(threading.RLock())
        self._lifecycle_state = "open"
        self._active_runs = 0
        self._active_run_tokens = set()
        self._run_lock = threading.Lock()
        self._deferred_close_thread = None
        self._close_errors = []
        self._close_initiator_token = None
        self._vision_executor = None
        self._vision_executor_lock = threading.RLock()
        self._pid = pid if pid is not None else os.getpid()
        self._create_managers()

    def _current_active_run_locked(self) -> _RuntimeRun | None:
        current_run = _CURRENT_RUNTIME_RUN.get()
        if (
            current_run is None
            or current_run.runtime is not self
            or current_run not in self._active_run_tokens
        ):
            return None
        return current_run

    def _start_close(self) -> tuple[str, _RuntimeRun | None]:
        with self._lifecycle:
            current_run = self._current_active_run_locked()
            if self._lifecycle_state == "closed":
                return "done", current_run
            if self._lifecycle_state == "closing":
                return "wait", current_run
            self._lifecycle_state = "closing"
            self._close_errors = []
            self._close_initiator_token = current_run
            self._bridge.start_closing()
            return "owner", current_run

    def _wait_until_closed(self) -> None:
        with self._lifecycle:
            while self._lifecycle_state != "closed":
                self._lifecycle.wait()

    def _wait_for_active_runs(self) -> None:
        with self._lifecycle:
            while self._active_runs:
                self._lifecycle.wait()

    def _finish_close(self) -> None:
        with self._lifecycle:
            self._lifecycle_state = "closed"
            self._lifecycle.notify_all()

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

    @staticmethod
    def _raise_first_error(errors: list[Exception]) -> None:
        if errors:
            raise errors[0]

    def _raise_close_error(self) -> None:
        self._raise_first_error(self._close_errors)

    @staticmethod
    async def _await_close_completion(close: Awaitable[None]) -> None:
        close_task = asyncio.create_task(close)
        cancellation: asyncio.CancelledError | None = None
        current_task = asyncio.current_task()
        initial_cancels = current_task.cancelling() if current_task else 0
        close_error: Exception | None = None
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError as exc:
                if cancellation is None:
                    cancellation = exc
            except Exception as exc:
                close_error = exc

        if close_error is None:
            try:
                close_task.result()
            except Exception as exc:
                close_error = exc

        if cancellation is not None:
            raise cancellation
        if current_task is not None and current_task.cancelling() > initial_cancels:
            raise asyncio.CancelledError
        if close_error is not None:
            raise close_error

    def _finish_bridge_close(self, errors: list[Exception]) -> None:
        try:
            self._bridge.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            self._finish_close()

    def _close_worker_external(self) -> None:
        errors = self._close_errors
        self._wait_for_active_runs()
        try:
            self._bridge.run_closing(lambda: self._close_async_resources(errors))
        except Exception as exc:
            errors.append(exc)
        self._close_sync_resources(errors)
        self._close_vision_executor(errors)
        self._finish_bridge_close(errors)
        self._raise_first_error(errors)

    def _defer_bridge_finish(
        self,
        errors: list[Exception],
        cleanup_finished: threading.Event | None = None,
    ) -> None:
        def finish_after_current_submission() -> None:
            if cleanup_finished is not None:
                cleanup_finished.wait()
            self._wait_for_active_runs()
            self._finish_bridge_close(errors)

        close_thread = threading.Thread(
            target=finish_after_current_submission,
            name="knowledge-async-loop-shutdown",
            daemon=True,
        )
        with self._lifecycle:
            self._deferred_close_thread = close_thread
        close_thread.start()

    async def _close_from_bridge(self, *, surface_errors: bool) -> None:
        errors = self._close_errors
        await self._close_async_resources(errors)
        await asyncio.to_thread(self._close_sync_resources, errors)
        await asyncio.to_thread(self._close_vision_executor, errors)
        self._defer_bridge_finish(errors)
        if surface_errors:
            self._raise_first_error(errors)

    async def _close_from_bridge_background(
        self,
        errors: list[Exception],
        cleanup_finished: threading.Event,
    ) -> None:
        try:
            await self._close_async_resources(errors)
            await asyncio.to_thread(self._close_sync_resources, errors)
            await asyncio.to_thread(self._close_vision_executor, errors)
        finally:
            cleanup_finished.set()

    async def _close_api_runtime(self) -> None:
        errors = self._close_errors
        await self._close_async_resources(errors)
        await asyncio.to_thread(self._close_sync_resources, errors)
        await asyncio.to_thread(self._close_vision_executor, errors)
        await asyncio.to_thread(self._finish_bridge_close, errors)
        self._raise_first_error(errors)

    async def aclose(self) -> None:
        close_state, current_run = self._start_close()
        if close_state == "done":
            self._raise_close_error()
            return
        if close_state == "wait":
            if current_run is not None or self._bridge.is_current_thread():
                return
            await self._await_close_completion(
                asyncio.to_thread(self._wait_until_closed)
            )
            self._raise_close_error()
            return
        if self._bridge.is_current_thread():
            await self._await_close_completion(
                self._close_from_bridge(surface_errors=True)
            )
            return
        with self._lifecycle:
            worker_bridge_owned = self._bridge.started or self._active_runs > 0
        if worker_bridge_owned:
            await self._await_close_completion(
                asyncio.to_thread(self._close_worker_external)
            )
            return
        await self._await_close_completion(self._close_api_runtime())

    def close_sync(self) -> None:
        close_state, current_run = self._start_close()
        if close_state == "done":
            self._raise_close_error()
            return
        if close_state == "wait":
            if current_run is not None or self._bridge.is_current_thread():
                return
            self._wait_until_closed()
            self._raise_close_error()
            return
        if self._bridge.is_current_thread():
            errors = self._close_errors
            cleanup_finished = threading.Event()
            self._defer_bridge_finish(errors, cleanup_finished)
            asyncio.get_running_loop().create_task(
                self._close_from_bridge_background(errors, cleanup_finished)
            )
            return
        self._close_worker_external()


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
