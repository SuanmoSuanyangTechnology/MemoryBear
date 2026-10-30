"""Background loops: periodic ``tick`` (idle pause / TTL / watermark) and ``reconcile``.

A failed round is logged and the loop keeps going; only ``stop`` ends it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from ..clock import Clock
from .manager import SandboxManager
from .scheduler_config import SchedulerConfig

logger = logging.getLogger(__name__)


class BackgroundRunner:
    def __init__(
        self,
        manager: SandboxManager,
        config: SchedulerConfig | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._manager = manager
        self._cfg = config or SchedulerConfig()
        self._clock = clock or Clock()
        self._tasks: list[asyncio.Task[None]] = []
        self.rounds: dict[str, int] = {"tick": 0, "reconcile": 0}
        self.failures: dict[str, int] = {"tick": 0, "reconcile": 0}

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    def start(self) -> None:
        if self._tasks:
            raise RuntimeError("runner already started")
        self._tasks = [
            asyncio.create_task(
                self._loop("tick", self._manager.tick, self._cfg.tick_interval_s),
                name="sandbox-tick",
            ),
            asyncio.create_task(
                self._loop("reconcile", self._manager.reconcile, self._cfg.reconcile_interval_s),
                name="sandbox-reconcile",
            ),
        ]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(
        self, name: str, fn: Callable[[], Awaitable[object]], interval_s: float
    ) -> None:
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failures[name] += 1
                logger.exception("background %s failed; will retry next round", name)
            self.rounds[name] += 1
            await self._clock.sleep(interval_s)
