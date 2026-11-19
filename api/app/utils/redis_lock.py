"""兼容现有 API 导入；跨进程用户写锁算法由共享 SDK 统一维护。"""

import os
from functools import lru_cache

from redis import Redis
from task_bridge.redis_fair_lock import (
    ACQUIRE_SCRIPT,
    RENEW_SCRIPT,
    UNLOCK_SCRIPT,
    RedisFairLock,
    _ensure_str,
)

__all__ = [
    "ACQUIRE_SCRIPT", "RENEW_SCRIPT", "UNLOCK_SCRIPT", "RedisFairLock", "_ensure_str",
    "get_memory_write_redis",
]


@lru_cache(maxsize=1)
def _memory_write_redis(pid: int) -> Redis:
    from app.core.config import settings

    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        db=settings.REDIS_DB_CELERY_BACKEND,
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
    )


def get_memory_write_redis() -> Redis:
    """用户归并使用与反思和迁移相同的 backend Redis DB。"""
    return _memory_write_redis(os.getpid())
