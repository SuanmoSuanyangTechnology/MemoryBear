"""Redis helper compatibility surface for ported monolith code.

The monolith's ``app.aioRedis`` owns module-level connection pools built from
``settings.REDIS_*``. This service owns the same Redis instance through
``runtime.redis`` (``RedisManager``), so these helpers delegate there and keep
the call-site contract identical: the cache helpers swallow transport errors
and return ``None``/``False`` so callers keep their existing fallback paths.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis as redis_sync

logger = logging.getLogger(__name__)


def _sync_redis() -> redis_sync.Redis:
    from .runtime import get_process_runtime

    return get_process_runtime().redis.sync_client()


async def get_thread_safe_redis():
    """Return the process-wide async client (bound to the current event loop)."""

    from .runtime import get_process_runtime

    return await get_process_runtime().redis.client()


def get_thread_safe_sync_redis() -> redis_sync.Redis:
    """Return the process-wide synchronous client."""

    return _sync_redis()


async def get_redis_connection():
    """Return the lazy async client, or ``None`` when runtime is unavailable."""

    try:
        return await get_thread_safe_redis()
    except Exception as exc:
        logger.error("Redis connection failed: %s", exc)
        return None


async def aio_redis_set(key: str, val: str | dict, expire: int = None):
    """Set a key; ``dict`` values are JSON-encoded like the monolith helper."""

    try:
        if isinstance(val, dict):
            val = json.dumps(val, ensure_ascii=False)
        client = await get_thread_safe_redis()
        if expire is not None:
            await client.set(key, val, ex=expire)
        else:
            await client.set(key, val)
    except Exception as exc:
        logger.error("Redis set error: %s", exc)


async def aio_redis_get(key: str):
    """Get a key; returns ``None`` on any failure."""

    try:
        client = await get_thread_safe_redis()
        return await client.get(key)
    except Exception as exc:
        logger.error("Redis get error: %s", exc)
        return None


async def aio_redis_delete(key: str):
    """Delete a key; returns ``None`` on any failure."""

    try:
        client = await get_thread_safe_redis()
        return await client.delete(key)
    except Exception as exc:
        logger.error("Redis delete error: %s", exc)
        return None


async def aio_redis_publish(channel: str, message: dict[str, Any]) -> bool:
    """Publish a JSON message; returns ``False`` on any failure."""

    try:
        conn = await get_redis_connection()
        if not conn:
            return False
        await conn.publish(channel, json.dumps(message, ensure_ascii=False))
        return True
    except Exception as exc:
        logger.error("Redis publish error: %s", exc)
        return False


class _AsyncRedisProxy:
    """Surrogate for the monolith's module-level ``aio_redis`` client.

    The monolith binds one ``redis.asyncio.StrictRedis`` at import time; ported
    call sites keep that shape through this proxy, which resolves the
    runtime-owned client per call so loop binding stays with ``RedisManager``.
    """

    async def set(self, key: str, value: Any, **kwargs: Any):
        client = await get_thread_safe_redis()
        return await client.set(key, value, **kwargs)

    async def get(self, key: str):
        client = await get_thread_safe_redis()
        return await client.get(key)

    async def delete(self, *keys: str):
        client = await get_thread_safe_redis()
        return await client.delete(*keys)

    async def execute_lua(self, script: str, numkeys: int, *keys_and_args: Any):
        """Run a server-side Lua script (the monolith's ``aio_redis.eval``)."""

        client = await get_thread_safe_redis()
        return await client.execute_command("EVAL", script, numkeys, *keys_and_args)


aio_redis = _AsyncRedisProxy()
