"""Redis 连接（惰性初始化，由 lifespan 显式控制生命周期）。

decode_responses=True：get/set 在 Redis 侧自动 str 编解码，消费者（如 ACL 规则加载）
拿到的是字符串，无需各自处理 bytes。get_redis 作为零参 async callable 传给鉴权
中间件，匹配企业侧 _resolve_redis 的 Callable 分支（惰性取值，避免 app 构建期建连）。

同步客户端 get_sync_redis 供 @redis_cache 的同步分支 / invalidate_cache_sync 使用：
进程内单例共享连接池。redis-py 的 ConnectionPool 本身线程安全，且自带 fork 检测
（connection.py 的 _fork_lock/_checkpid，实例化时 os.register_after_fork），故不需要
老单体 aioRedis.py 那套 thread-local 多池——那套是为 Celery 线程/进程池下 **async**
连接不能跨 event loop 复用而设；sync 客户端无此约束，每线程一池反而按线程数放大连接数。
"""

import redis as sync_redis
import redis.asyncio as aioredis

from src.config import settings

_client = None
_sync_client = None


async def get_redis():
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=settings.REDIS_POOL_SIZE,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
    return _client


def get_sync_redis():
    """同步 Redis 客户端（进程内单例，惰性建连）。

    与异步客户端共用 REDIS_URL/decode_responses，两条路径读到同一份 str 编码值。
    连接池由本客户端自建（redis.from_url → auto_close_connection_pool=True），
    故 close() 会连带关闭连接池（client.py:791）。
    """
    global _sync_client
    if _sync_client is None:
        _sync_client = sync_redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=settings.REDIS_POOL_SIZE,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            health_check_interval=30,
        )
    return _sync_client


async def init_redis():
    await (await get_redis()).ping()


async def close_redis():
    global _client, _sync_client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _sync_client is not None:
        _sync_client.close()
        _sync_client = None