"""Loop-safe asynchronous Redis lifecycle."""

from __future__ import annotations

import asyncio
import os
import threading

import redis as redis_sync
import redis.asyncio as redis_async

from ..config import KnowledgeSettings


class RedisManager:
    """Lazily own one Redis client for one process and event loop."""

    def __init__(self, settings: KnowledgeSettings):
        self._settings = settings
        self._client: redis_async.Redis | None = None
        self._sync_client: redis_sync.Redis | None = None
        self._pid = os.getpid()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    @property
    def sync_initialized(self) -> bool:
        return self._sync_client is not None

    async def client(self) -> redis_async.Redis:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Redis client cannot be shared across event loops")
        async with self._lock:
            if self._client is None:
                self._client = redis_async.Redis.from_url(
                    self._settings.redis_url,
                    decode_responses=True,
                    max_connections=self._settings.kb_redis_pool_size,
                    health_check_interval=30,
                )
                self._pid = os.getpid()
                self._loop = current_loop
            return self._client

    def sync_client(self) -> redis_sync.Redis:
        with self._sync_lock:
            if self._sync_client is None:
                self._sync_client = redis_sync.Redis.from_url(
                    self._settings.redis_url,
                    decode_responses=True,
                    max_connections=self._settings.kb_redis_pool_size,
                    health_check_interval=30,
                )
                self._pid = os.getpid()
            return self._sync_client

    async def ping(self) -> bool:
        return bool(await (await self.client()).ping())

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            await client.aclose()

    def close_sync(self) -> None:
        with self._sync_lock:
            client = self._sync_client
            self._sync_client = None
        if client is not None:
            client.close()

    def reset_after_fork(self) -> None:
        self._client = None
        self._sync_client = None
        self._loop = None
        self._pid = os.getpid()
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()
