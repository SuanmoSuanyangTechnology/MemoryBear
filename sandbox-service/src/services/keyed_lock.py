"""Per-key asyncio locks with reference counting.

A plain ``dict[str, asyncio.Lock]`` cannot be pruned safely: a released lock whose
next waiter has not resumed yet reports ``locked() == False``. Here an entry lives
while anybody holds *or waits for* it, so ``busy`` is exact and entries never leak.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class _Entry:
    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.refs = 0


class KeyedLocks:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def busy(self, key: str) -> bool:
        """True while somebody holds or waits for ``key``."""

        entry = self._entries.get(key)
        return entry is not None and entry.refs > 0

    def size(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = _Entry()
        entry.refs += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.refs -= 1
            if entry.refs == 0 and self._entries.get(key) is entry:
                del self._entries[key]

    @asynccontextmanager
    async def try_hold(self, key: str) -> AsyncIterator[bool]:
        """Hold ``key`` only if nobody else does; yields whether the lock was taken.

        The busy check and the acquire happen without an intervening suspension
        point, so this is atomic on a single event loop.
        """

        if self.busy(key):
            yield False
            return
        async with self.hold(key):
            yield True
