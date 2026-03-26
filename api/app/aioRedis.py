import asyncio
import json
import logging
from typing import Dict, Any, Optional

from redis.asyncio import Redis, ConnectionPool

from app.core.config import settings

# 设置日志记录器
logger = logging.getLogger(__name__)

# 存储每个 event loop 的连接池和客户端
_loop_clients: Dict[int, Redis] = {}


def get_redis_client() -> Redis:
    """获取当前 event loop 的 Redis 客户端
    
    每个 event loop 会获得自己的连接池和客户端实例。
    这样可以避免连接对象跨 loop 使用导致的 'attached to a different loop' 错误。
    """
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        
        # 检查是否已有该 loop 的客户端
        if loop_id not in _loop_clients:
            # 为当前 loop 创建新的连接池和客户端
            pool = ConnectionPool.from_url(
                f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}",
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,
                max_connections=30
            )
            client = Redis(connection_pool=pool)
            _loop_clients[loop_id] = client
            logger.debug(f"为 event loop {loop_id} 创建新的 Redis 客户端和连接池")
        
        return _loop_clients[loop_id]
    except RuntimeError:
        # 没有运行中的 loop，创建临时客户端
        logger.warning("没有运行中的 event loop，创建临时 Redis 客户端")
        pool = ConnectionPool.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}",
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            max_connections=30
        )
        return Redis(connection_pool=pool)


# 全局 Redis 客户端代理类
class RedisProxy:
    """Redis 代理类，自动获取当前 event loop 的客户端"""
    
    def __getattr__(self, name):
        client = get_redis_client()
        return getattr(client, name)


# 全局实例
aio_redis = RedisProxy()


async def get_redis_connection():
    """获取 Redis 连接"""
    try:
        return get_redis_client()
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
        client = get_redis_client()
        if isinstance(val, dict):
            val = json.dumps(val, ensure_ascii=False)

        if expire is not None:
            await client.set(key, val, ex=expire)
        else:
            await client.set(key, val)
    except Exception as e:
        logger.error(f"Redis set错误: {str(e)}")


async def aio_redis_get(key: str):
    """获取Redis键值"""
    try:
        client = get_redis_client()
        return await client.get(key)
    except Exception as e:
        logger.error(f"Redis get错误: {str(e)}")
        return None


async def aio_redis_delete(key: str):
    """删除Redis键"""
    try:
        client = get_redis_client()
        return await client.delete(key)
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
