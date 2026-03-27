import asyncio
import json
import logging
import os
import threading
from typing import Dict, Any, Optional

import redis.asyncio as redis
from redis.asyncio import ConnectionPool

from app.core.config import settings

# 设置日志记录器
logger = logging.getLogger(__name__)

# 创建连接池
pool = ConnectionPool.from_url(
    f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}",
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD,
    decode_responses=True,
    max_connections=30
)
aio_redis = redis.StrictRedis(connection_pool=pool)

_REDIS_URL = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}"

# Thread-local storage for connection pools.
# Each thread (and each forked process) gets its own pool to avoid
# "Future attached to a different loop" errors in Celery --pool=threads
# and stale connections after fork in --pool=prefork.
_thread_local = threading.local()


def get_thread_safe_redis() -> redis.StrictRedis:
    """Get a Redis client safe for the current execution context.
    
    Uses thread-local storage with PID checking to ensure:
    - Each thread gets its own ConnectionPool (Celery --pool=threads)
    - Pools are recreated after fork (Celery --pool=prefork)
    - health_check_interval prevents stale connection errors
    
    Returns:
        redis.StrictRedis: A Redis client with a thread/process-local pool.
    """
    current_pid = os.getpid()

    if not hasattr(_thread_local, "pool") or getattr(_thread_local, "pid", None) != current_pid:
        _thread_local.pid = current_pid
        _thread_local.pool = ConnectionPool.from_url(
            _REDIS_URL,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            max_connections=5,
            health_check_interval=30,
        )

    return redis.StrictRedis(connection_pool=_thread_local.pool)


async def get_redis_connection():
    """获取Redis连接"""
    try:
        return redis.StrictRedis(connection_pool=pool)
    except Exception as e:
        logger.error(f"Redis连接失败: {str(e)}")
        return None


async def aio_redis_set(key: str, val: str | dict, expire: int = None):
    """设置Redis键值
    
    Args:
        key: Redis键
        val: 要存储的值(字符串或字典)
        expire: 过期时间(秒)，None表示永不过期
    """
    try:
        if isinstance(val, dict):
            val = json.dumps(val, ensure_ascii=False)

        if expire is not None:
            await aio_redis.set(key, val, ex=expire)
        else:
            await aio_redis.set(key, val)
    except Exception as e:
        logger.error(f"Redis set错误: {str(e)}")


async def aio_redis_get(key: str):
    """获取Redis键值"""
    try:
        return await aio_redis.get(key)
    except Exception as e:
        logger.error(f"Redis get错误: {str(e)}")
        return None


async def aio_redis_delete(key: str):
    """删除Redis键"""
    try:
        return await aio_redis.delete(key)
    except Exception as e:
        logger.error(f"Redis delete错误: {str(e)}")
        return None


async def aio_redis_publish(channel: str, message: Dict[str, Any]) -> bool:
    """发布消息到Redis频道"""
    try:
        conn = await get_redis_connection()
        if not conn:
            return False
        await conn.publish(channel, json.dumps(message, ensure_ascii=False))
        return True
    except Exception as e:
        logger.error(f"Redis发布错误: {str(e)}")
        return False


class RedisSubscriber:
    """Redis订阅器"""

    def __init__(self, channel: str):
        self.channel = channel
        self.conn = None
        self.pubsub = None
        self.is_closed = False
        self._queue = asyncio.Queue()
        self._task = None

    async def start(self):
        """开始订阅"""
        if self.is_closed or self._task:
            return

        self._task = asyncio.create_task(self._receive_messages())
        logger.info(f"开始订阅: {self.channel}")

    async def _receive_messages(self):
        """接收消息"""
        try:
            self.conn = await get_redis_connection()
            if not self.conn:
                return

            self.pubsub = self.conn.pubsub()
            await self.pubsub.subscribe(self.channel)

            while not self.is_closed:
                try:
                    message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
                    if message and isinstance(message.get("data"), str):
                        try:
                            await self._queue.put(json.loads(message["data"]))
                        except json.JSONDecodeError:
                            logger.warning(f"消息解析失败: {message['data']}")
                    await asyncio.sleep(0.01)
                except Exception as e:
                    if "closed" in str(e).lower():
                        break
                    logger.warning(f"接收消息错误: {str(e)}")
                    await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"订阅错误: {str(e)}")
            await self._queue.put({"type": "error", "data": {"message": str(e), "status": "error"}})
        finally:
            await self._queue.put(None)
            await self._cleanup()

    async def _cleanup(self):
        """清理资源"""
        if self.pubsub:
            try:
                await self.pubsub.unsubscribe(self.channel)
                await self.pubsub.close()
            except Exception:
                pass
        if self.conn:
            try:
                await self.conn.close()
            except Exception:
                pass

    async def get_message(self) -> Optional[Dict[str, Any]]:
        """获取消息"""
        if self.is_closed:
            return None
        if not self._task:
            await self.start()
        try:
            return await self._queue.get()
        except Exception as e:
            logger.error(f"获取消息错误: {str(e)}")
            return None

    async def close(self):
        """关闭订阅器"""
        if self.is_closed:
            return
        self.is_closed = True
        if self._task:
            self._task.cancel()
        await self._cleanup()


class RedisPubSubManager:
    """Redis发布订阅管理器"""

    def __init__(self):
        self.subscribers = {}

    async def publish(self, channel: str, message: Dict[str, Any]) -> bool:
        return await aio_redis_publish(channel, message)

    def get_subscriber(self, channel: str) -> RedisSubscriber:
        if channel in self.subscribers:
            subscriber = self.subscribers[channel]
            if not subscriber.is_closed:
                return subscriber

        subscriber = RedisSubscriber(channel)
        self.subscribers[channel] = subscriber
        return subscriber

    def cancel_subscription(self, channel: str) -> bool:
        if channel in self.subscribers:
            asyncio.create_task(self.subscribers[channel].close())
            del self.subscribers[channel]
            return True
        return False

    def cancel_all_subscriptions(self) -> int:
        count = len(self.subscribers)
        for subscriber in self.subscribers.values():
            asyncio.create_task(subscriber.close())
        self.subscribers.clear()
        return count


# 全局实例
pubsub_manager = RedisPubSubManager()
