"""Process-level ownership of model service infrastructure."""

from __future__ import annotations

import asyncio
import os

from .config import ModelServiceSettings
from .db import DatabaseManager
from .infrastructure import ModelRuntimeManager, RedisManager


class ProcessRuntime:
    """Own all lazy resources for exactly one operating-system process."""

    def __init__(self, settings: ModelServiceSettings) -> None:
        self.settings = settings
        self._pid = os.getpid()
        self.database = DatabaseManager(settings)
        self.redis = RedisManager(settings)
        self.model_runtime = ModelRuntimeManager(settings)

    @property
    def pid(self) -> int:
        return self._pid

    def _close_sync_resources(self) -> None:
        self.redis.close_sync()
        self.database.close_sync()

    async def aclose(self) -> None:
        errors: list[Exception] = []
        for close in (
            self.model_runtime.aclose,
            self.redis.aclose,
            self.database.aclose_async,
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
