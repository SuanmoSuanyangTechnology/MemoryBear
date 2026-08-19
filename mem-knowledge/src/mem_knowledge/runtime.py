"""Process-level ownership of knowledge service infrastructure."""

from __future__ import annotations

import asyncio
import os
import threading

from .bootstrap import get_settings
from .config import KnowledgeSettings
from .db import DatabaseManager
from .infrastructure import (
    ElasticsearchManager,
    ModelRuntimeManager,
    RedisManager,
    StorageManager,
)


class ProcessRuntime:
    """Own all lazy resources for exactly one operating-system process."""

    def __init__(self, settings: KnowledgeSettings):
        self.settings = settings
        self._pid = os.getpid()
        self._closed = False
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

    def reset_after_fork(self, pid: int | None = None) -> None:
        self.database.reset_after_fork()
        self.redis.reset_after_fork()
        self.elasticsearch.reset_after_fork()
        self.storage.reset_after_fork()
        self.model_runtime.reset_after_fork()
        self._pid = pid if pid is not None else os.getpid()
        self._closed = False
        self._create_managers()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        for close in (
            self.model_runtime.aclose,
            self.storage.aclose,
            self.redis.aclose,
            self.elasticsearch.aclose,
            self.database.aclose,
        ):
            try:
                await close()
            except Exception as exc:
                errors.append(exc)
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
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(shutdown_worker_runtime())
        return
    raise RuntimeError(
        "shutdown_worker_runtime_sync cannot run inside an event loop"
    )
