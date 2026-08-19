"""Loop-safe asynchronous Redis lifecycle."""

from __future__ import annotations

import asyncio
import os

import redis.asyncio as redis

from ..config import KnowledgeSettings


class RedisManager:
    """Lazily own one Redis client for one process and event loop."""

    def __init__(self, settings: KnowledgeSettings):
        self._settings = settings
        self._client: redis.Redis | None = None
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    async def client(self) -> redis.Redis:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Redis client cannot be shared across event loops")
        async with self._lock:
            if self._client is None:
                self._client = redis.Redis.from_url(
                    self._settings.redis_url,
                    decode_responses=True,
                    max_connections=self._settings.redis_pool_size,
                    health_check_interval=30,
                )
                self._pid = os.getpid()
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

    def reset_after_fork(self) -> None:
        self._client = None
        self._loop = None
        self._pid = os.getpid()
        self._lock = asyncio.Lock()
