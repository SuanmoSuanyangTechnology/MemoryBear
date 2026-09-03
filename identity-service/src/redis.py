"""Redis 连接（惰性初始化，由 lifespan/boot fixture 显式控制）。decode_responses=False：
快照为 bytes 语义，消费者侧自行 JSON 解码。

socket 超时与 gateway 对齐：后台循环（pubsub/reconcile/audit/retention）在 TCP
半开连接下会无限阻塞——超时保证单命令最多 2s 返回（或抛异常走重连），防止
「禁用即时生效」这类安全语义因连接挂死而失效。pubsub 订阅在 socket_timeout
触发时会抛 TimeoutError（RedisError 子类），由 notify.subscribe 的重连逻辑兜底。
"""
import redis.asyncio as aioredis
from src.config import settings

redis = None

async def init_redis():
    global redis
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=False,
                              socket_connect_timeout=2.0, socket_timeout=2.0)
    await redis.ping()  # 启动 fail-fast：Redis 不可达时 lifespan 直接失败，不留半开连接

async def close_redis():
    if redis is not None:
        await redis.aclose()
