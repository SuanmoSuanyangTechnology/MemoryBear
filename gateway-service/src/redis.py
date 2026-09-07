"""Redis 连接（惰性初始化，由 lifespan/boot fixture 显式控制）。decode_responses=False：
快照为 bytes 语义，消费者侧自行 JSON 解码。"""
import redis.asyncio as aioredis
from src.config import settings

redis = None

async def init_redis():
    global redis
    # 惰性建连 + 无预热时，首个请求承担建连成本（远程 Redis ~275ms），
    # 超过快照读取的 fail-closed 超时窗口（REDIS_CMD_TIMEOUT_MS）→ 全部 401
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=False,
                              socket_connect_timeout=2.0, socket_timeout=2.0)
    await redis.ping()

async def close_redis():
    if redis is not None:
        await redis.aclose()
