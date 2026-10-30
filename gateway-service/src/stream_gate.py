"""Bounded concurrent-stream gate shared by the HTTP and gRPC forwarders."""

from __future__ import annotations

import asyncio

from .metrics import gateway_streaming_active_connections


class StreamGate:
    """One concurrency budget for every streaming response leaving the gateway.

    Both transports hold a slot for the whole stream lifetime (not just until
    response headers): releasing earlier would let the real concurrency and the
    active-connection gauge drift to zero while streams are still running.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
        gateway_streaming_active_connections.inc()
        return True

    async def release(self) -> None:
        async with self._lock:
            if self._active <= 0:
                return
            self._active -= 1
        gateway_streaming_active_connections.dec()
