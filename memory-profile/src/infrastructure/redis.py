"""Loop-safe asynchronous Redis lifecycle."""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from src.config import ProfileSettings


class RedisManager:
    """Lazily own one Redis client for one process and event loop."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._client: Redis | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    async def client(self) -> Redis:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Redis client cannot be shared across event loops")
        async with self._lock:
            if self._client is None:
                self._client = Redis.from_url(
                    self._settings.resolved_redis_url,
                    decode_responses=True,
                    max_connections=self._settings.redis_pool_size,
                    health_check_interval=30,
                )
                self._loop = current_loop
            return self._client

    async def ping(self) -> bool:
        return bool(await (await self.client()).ping())

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            await client.aclose()
