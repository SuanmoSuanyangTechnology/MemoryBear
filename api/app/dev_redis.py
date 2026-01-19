"""
开发专用 Redis 连接
使用独立的 Redis 数据库（DB 14）用于开发测试，不影响生产环境的缓存数据
"""
import asyncio
import json
import logging
from typing import Dict, Any, Optional
import redis.asyncio as redis
from redis.asyncio import ConnectionPool

from app.core.config import settings

# 设置日志记录器
logger = logging.getLogger(__name__)


# 创建开发专用连接池（使用 DB 14）
dev_pool = ConnectionPool.from_url(
    f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}",
    db=settings.DEV_REDIS_DB,  # 使用开发数据库
    password=settings.REDIS_PASSWORD,
    decode_responses=True,
    max_connections=10  # 开发环境连接数较少
)
dev_redis = redis.StrictRedis(connection_pool=dev_pool)


async def get_dev_redis_connection():
    """获取开发 Redis 连接"""
    try:
        return redis.StrictRedis(connection_pool=dev_pool)
    except Exception as e:
        logger.error(f"开发 Redis 连接失败: {str(e)}")
        return None


async def dev_redis_set(key: str, val: str | dict, expire: int = None):
    """设置开发 Redis 键值
    
    Args:
        key: Redis键
        val: 要存储的值(字符串或字典)
        expire: 过期时间(秒)，None表示永不过期
    """
    try:
        if isinstance(val, dict):
            val = json.dumps(val, ensure_ascii=False)
        
        if expire is not None:
            await dev_redis.set(key, val, ex=expire)
        else:
            await dev_redis.set(key, val)
        logger.debug(f"开发 Redis set成功: {key}")
    except Exception as e:
        logger.error(f"开发 Redis set错误: {str(e)}")


async def dev_redis_get(key: str):
    """获取开发 Redis 键值"""
    try:
        result = await dev_redis.get(key)
        logger.debug(f"开发 Redis get: {key} = {result}")
        return result
    except Exception as e:
        logger.error(f"开发 Redis get错误: {str(e)}")
        return None


async def dev_redis_delete(key: str):
    """删除开发 Redis 键"""
    try:
        result = await dev_redis.delete(key)
        logger.debug(f"开发 Redis delete: {key}")
        return result
    except Exception as e:
        logger.error(f"开发 Redis delete错误: {str(e)}")
        return None


async def dev_redis_keys(pattern: str = "*"):
    """获取开发 Redis 所有匹配的键
    
    Args:
        pattern: 匹配模式，默认为所有键
    """
    try:
        keys = await dev_redis.keys(pattern)
        logger.debug(f"开发 Redis keys: {pattern} 找到 {len(keys)} 个键")
        return keys
    except Exception as e:
        logger.error(f"开发 Redis keys错误: {str(e)}")
        return []


async def dev_redis_clear_all():
    """清空开发 Redis 数据库的所有数据（谨慎使用！）"""
    try:
        await dev_redis.flushdb()
        logger.warning("开发 Redis 数据库已清空")
        return True
    except Exception as e:
        logger.error(f"开发 Redis 清空错误: {str(e)}")
        return False


async def migrate_to_production():
    """
    将开发数据库（DB 14）的数据迁移到生产数据库（DB 0）
    
    注意：这是一个辅助工具函数，实际使用时需要谨慎
    """
    from app.aioRedis import aio_redis
    
    try:
        # 获取所有键
        keys = await dev_redis.keys("*")
        migrated_count = 0
        
        for key in keys:
            # 获取值和TTL
            value = await dev_redis.get(key)
            ttl = await dev_redis.ttl(key)
            
            # 写入生产数据库
            if ttl > 0:
                await aio_redis.set(key, value, ex=ttl)
            else:
                await aio_redis.set(key, value)
            
            migrated_count += 1
        
        logger.info(f"数据迁移完成: 迁移了 {migrated_count} 个键")
        return migrated_count
    except Exception as e:
        logger.error(f"数据迁移失败: {str(e)}")
        return 0


# 开发环境信息
async def dev_redis_info():
    """获取开发 Redis 数据库信息"""
    try:
        info = {
            "host": settings.REDIS_HOST,
            "port": settings.REDIS_PORT,
            "db": settings.DEV_REDIS_DB,
            "connected": await dev_redis.ping(),
            "keys_count": len(await dev_redis.keys("*"))
        }
        return info
    except Exception as e:
        logger.error(f"获取开发 Redis 信息失败: {str(e)}")
        return None
