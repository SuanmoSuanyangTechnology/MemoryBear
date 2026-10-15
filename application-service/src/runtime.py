"""Process-level ownership of application service infrastructure."""

from __future__ import annotations

import asyncio

from .config import AppServiceSettings
from .db import DatabaseManager
from .infrastructure import RedisManager
from .infrastructure.upstream import MonolithUpstream
from .integrations.model_invoke import ModelInvokeClient


class ProcessRuntime:
    """Own all lazy resources for exactly one operating-system process."""

    def __init__(self, settings: AppServiceSettings) -> None:
        self.settings = settings
        self.database = DatabaseManager(settings)
        self.redis = RedisManager(settings)
        self.upstream = MonolithUpstream.from_settings(settings)
        self.model_invoke = ModelInvokeClient.from_settings(settings)

    def _close_sync_resources(self) -> None:
        self.model_invoke.close()
        self.upstream.close()
        self.redis.close_sync()
        self.database.close_sync()

    async def aclose(self) -> None:
        errors: list[Exception] = []
        for close in (
            self.redis.aclose,
            self.database.aclose_async,
            self.upstream.aclose,
        ):
            try:
                await close()
            except Exception as exc:
                errors.append(exc)
        try:
            await asyncio.to_thread(self._close_sync_resources)
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise errors[0]


_process_runtime: ProcessRuntime | None = None


def set_process_runtime(runtime: ProcessRuntime) -> None:
    """Record the process runtime at assembly time.

    Deep workflow-runtime code (engine/node modules carried over from the
    monolith) resolves database/Redis handles without a FastAPI request, the
    same way the monolith reads its module-level singletons. ``create_app``
    publishes the runtime here so those call sites keep working.
    """

    global _process_runtime
    _process_runtime = runtime


def get_process_runtime() -> ProcessRuntime:
    """Return the process runtime recorded by ``create_app``; fail closed when
    the application has not been assembled."""

    if _process_runtime is None:
        raise RuntimeError("process runtime is not initialized; create_app must run first")
    return _process_runtime
