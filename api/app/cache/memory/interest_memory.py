"""
Interest Distribution Cache

兴趣分布缓存模块
用于缓存用户的兴趣分布标签数据，避免重复调用模型生成
"""
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.aioRedis import aio_redis

logger = logging.getLogger(__name__)

# 缓存过期时间：24小时
INTEREST_CACHE_EXPIRE = 86400


class InterestMemoryCache:
    """兴趣分布缓存类"""

    PREFIX = "cache:memory:interest_distribution"

    @classmethod
    def _get_key(cls, end_user_id: str, language: str) -> str:
        """生成 Redis key

        Args:
            end_user_id: 用户ID
            language: 语言类型

        Returns:
            完整的 Redis key
        """
        return f"{cls.PREFIX}:by_user:{end_user_id}:{language}"

    @classmethod
    async def set_interest_distribution(
        cls,
        end_user_id: str,
        language: str,
        data: List[Dict[str, Any]],
        expire: int = INTEREST_CACHE_EXPIRE,
    ) -> bool:
        """设置用户兴趣分布缓存

        Args:
            end_user_id: 用户ID
            language: 语言类型
            data: 兴趣分布列表，格式 [{"name": "...", "frequency": ...}, ...]
            expire: 过期时间（秒），默认24小时

        Returns:
            是否设置成功
        """
        try:
            key = cls._get_key(end_user_id, language)
            payload = {
                "data": data,
                "generated_at": datetime.now().isoformat(),
                "cached": True,
            }
            value = json.dumps(payload, ensure_ascii=False)
            
            # 使用 set ex 参数，aio_redis 通过 RedisProxy 自动获取当前 event loop 的客户端
            await aio_redis.set(key, value, ex=expire)
            
            logger.info(f"设置兴趣分布缓存成功: {key}, 过期时间: {expire}秒")
            return True
        except Exception as e:
            logger.error(f"设置兴趣分布缓存失败: {e}", exc_info=True)
            return False

    @classmethod
    async def get_interest_distribution(
        cls,
        end_user_id: str,
        language: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """获取用户兴趣分布缓存

        Args:
            end_user_id: 用户ID
            language: 语言类型

        Returns:
            兴趣分布列表，缓存不存在或已过期返回 None
        """
        try:
            key = cls._get_key(end_user_id, language)
            value = await aio_redis.get(key)
            if value:
                payload = json.loads(value)
                logger.info(f"命中兴趣分布缓存: {key}")
                return payload.get("data")
            logger.info(f"兴趣分布缓存不存在或已过期: {key}")
            return None
        except Exception as e:
            logger.error(f"获取兴趣分布缓存失败: {e}", exc_info=True)
            return None

    @classmethod
    async def delete_interest_distribution(
        cls,
        end_user_id: str,
        language: str,
    ) -> bool:
        """删除用户兴趣分布缓存

        Args:
            end_user_id: 用户ID
            language: 语言类型

        Returns:
            是否删除成功
        """
        try:
            key = cls._get_key(end_user_id, language)
            result = await aio_redis.delete(key)
            logger.info(f"删除兴趣分布缓存: {key}, 结果: {result}")
            return result > 0
        except Exception as e:
            logger.error(f"删除兴趣分布缓存失败: {e}", exc_info=True)
            return False
