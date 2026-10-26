"""Synchronous Redis lifecycle for Celery worker processes."""

from __future__ import annotations

import logging
import threading

from redis import Redis

from src.config import JobsSettings

logger = logging.getLogger(__name__)


class RedisManager:
    """Own one blocking Redis client per worker process.

    ``redis.Redis`` is thread-safe, so a ``--pool=threads`` worker can share
    this single client across its concurrency slots.
    """

    def __init__(self, settings: JobsSettings) -> None:
        self._settings = settings
        self._client: Redis | None = None
        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    def client(self) -> Redis:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = Redis.from_url(
                    self._settings.resolved_redis_url,
                    decode_responses=True,
                    max_connections=self._settings.redis_pool_size,
                    socket_timeout=self._settings.redis_socket_timeout_seconds,
                    socket_connect_timeout=self._settings.redis_connect_timeout_seconds,
                    health_check_interval=30,
                )
            return self._client

    def ping(self) -> bool:
        return bool(self.client().ping())

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

    def reset_after_fork(self) -> None:
        """Discard connections inherited from the parent process."""

        client = self._client
        self._client = None
        if client is not None:
            try:
                client.connection_pool.disconnect()
            except Exception as exc:
                logger.warning(
                    "Failed to disconnect inherited Redis connections: %s",
                    type(exc).__name__,
                    extra={"event": "redis_fork_reset_failed"},
                )

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.connection_pool.disconnect()


def _count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None
