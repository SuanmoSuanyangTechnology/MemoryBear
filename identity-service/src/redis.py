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
import redis.asyncio as aioredis

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
