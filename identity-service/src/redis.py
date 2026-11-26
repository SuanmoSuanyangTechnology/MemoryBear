"""Redis connection (lazy init, controlled by the lifespan / boot fixture).
decode_responses=False keeps snapshot payloads as bytes; consumers decode JSON.

Background loops (pubsub/reconcile/audit/retention) would block forever on a
half-open TCP connection. The 5s socket read timeout caps each command (or
raises, forcing a reconnect) and leaves headroom over the 300ms blocking
XREADGROUP plus link jitter; the 2s connect timeout stays short so an
unreachable Redis fails fast at startup instead of stalling the pool.
TimeoutError is a RedisError subclass, so the loops' catch/reconnect paths
already handle it.
"""
import os
import threading

import redis.asyncio as aioredis

import redis as sync_redis
from src.config import settings

redis = None

async def init_redis():
    global redis
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=False,
                              socket_connect_timeout=2.0, socket_timeout=5.0)
    await redis.ping()  # 启动 fail-fast：Redis 不可达时 lifespan 直接失败，不留半开连接

async def close_redis():
    if redis is not None:
        await redis.aclose()


_sync_thread_local = threading.local()


def get_thread_safe_sync_redis() -> sync_redis.StrictRedis:
    """Sync client bound to this thread and process (fork-safe, monolith parity).

    A forked worker or a fresh thread gets its own pool so sockets created in
    the parent process are never reused. decode_responses=True keeps session
    payloads as str, matching the monolith's sync client.
    """
    current_pid = os.getpid()
    if (
        getattr(_sync_thread_local, "pool", None) is None
        or getattr(_sync_thread_local, "pid", None) != current_pid
    ):
        _sync_thread_local.pid = current_pid
        _sync_thread_local.pool = sync_redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=settings.REDIS_POOL_SIZE,
            health_check_interval=30,
            socket_connect_timeout=2.0,
            socket_timeout=5.0,
        )
    return sync_redis.StrictRedis(connection_pool=_sync_thread_local.pool)


def close_sync_redis() -> None:
    pool = getattr(_sync_thread_local, "pool", None)
    if pool is not None:
        pool.disconnect()
        del _sync_thread_local.pool
        _sync_thread_local.pid = None
