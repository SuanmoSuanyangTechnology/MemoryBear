"""Injectable clock so TTL / queue / rate-limit logic is testable without sleeping."""

from __future__ import annotations

import asyncio
import time


class Clock:
    def time(self) -> float:
        return time.time()

    def now_ms(self) -> int:
        return int(self.time() * 1000)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ManualClock(Clock):
    """Deterministic clock: ``sleep`` advances time instead of waiting."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def sleep(self, seconds: float) -> None:
        self._now += max(0.0, seconds)
        await asyncio.sleep(0)
