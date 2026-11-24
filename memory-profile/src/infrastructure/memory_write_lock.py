"""Cross-service per-user write locks for the migrated end-user merge.

``RedisFairLock`` lives in the shared task-bridge SDK; only the client differs.
The lock must use the API's Celery-backend DB (``REDIS_DB_CELERY_BACKEND``) so
API and memory-jobs writers contend on the same ``memory_write:<id>`` key.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from functools import lru_cache

from redis import Redis
from task_bridge.redis_fair_lock import RedisFairLock

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

LOCK_EXPIRE_SECONDS = 1200
LOCK_TIMEOUT_SECONDS = 60


class MemoryWriteLockTimeout(RuntimeError):
    """A per-user graph write lock could not be acquired in time."""


@lru_cache(maxsize=4)
def _sync_client(url: str, pid: int) -> Redis:
    """One synchronous client per (URL, process); the lock API is blocking."""

    del pid
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
    )


def memory_write_redis(settings: ProfileSettings) -> Redis:
    return _sync_client(settings.memory_write_redis_url, os.getpid())


async def acquire_memory_write_locks(
    settings: ProfileSettings, end_user_ids: Iterable[object]
) -> list[RedisFairLock]:
    """Acquire every user's lock in sorted order; release on partial failure."""

    redis_client = memory_write_redis(settings)
    locks: list[RedisFairLock] = []
    for end_user_id in sorted({str(value) for value in end_user_ids}):
        lock = RedisFairLock(
            key=f"memory_write:{end_user_id}",
            redis_client=redis_client,
            expire=LOCK_EXPIRE_SECONDS,
            timeout=LOCK_TIMEOUT_SECONDS,
            auto_renewal=True,
        )
        if not await asyncio.to_thread(lock.acquire):
            await release_memory_write_locks(locks)
            raise MemoryWriteLockTimeout(
                f"Memory write lock timed out: {end_user_id}"
            )
        locks.append(lock)
    return locks


async def release_memory_write_locks(locks: list[RedisFairLock]) -> None:
    for lock in reversed(locks):
        try:
            await asyncio.to_thread(lock.release)
        except Exception:
            logger.exception(
                "[merge_end_users] 释放 memory_write 锁失败: key=%s", lock.key
            )


__all__ = [
    "MemoryWriteLockTimeout",
    "acquire_memory_write_locks",
    "memory_write_redis",
    "release_memory_write_locks",
]