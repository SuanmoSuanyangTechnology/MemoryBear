"""Loop-safe asynchronous Redis lifecycle."""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis

from src.config import ProfileSettings

logger = logging.getLogger(__name__)


def _count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None


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

    def pool_snapshot(self) -> dict[str, object] | None:
        """Return in-memory connection-pool counts without issuing a command."""

        client = self._client
        if client is None:
            return None
        try:
            pool = client.connection_pool
            return {
                "max_connections": getattr(pool, "max_connections", None),
                "in_use": _count(getattr(pool, "_in_use_connections", None)),
                "available": _count(getattr(pool, "_available_connections", None)),
            }
        except Exception as exc:
            logger.warning(
                "Failed to read Redis pool state: %s",
                type(exc).__name__,
                extra={"event": "redis_pool_snapshot_failed"},
            )
            return None

    async def ping(self) -> bool:
        return bool(await (await self.client()).ping())

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            await client.aclose()
