"""
Emotion Suggestions Cache

情绪个性化建议缓存模块
用于缓存用户的情绪个性化建议数据
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from app.aioRedis import aio_redis

logger = logging.getLogger(__name__)


class EmotionMemoryCache:
    """情绪建议缓存类"""
    
    # Key 前缀
    PREFIX = "cache:memory:emotion_memory"
    
    @classmethod
    def _get_key(cls, *parts: str) -> str:
        """生成 Redis key
        
        Args:
            *parts: key 的各个部分
            
        Returns:
            完整的 Redis key
        """
        return ":".join([cls.PREFIX] + list(parts))
    
    @classmethod
    async def set_emotion_suggestions(
        cls,
        user_id: str,
        suggestions_data: Dict[str, Any],
        expire: int = 86400
    ) -> bool:
        """设置用户情绪建议缓存
        
        Args:
            user_id: 用户ID（end_user_id）
            suggestions_data: 建议数据字典，包含：
                - health_summary: 健康状态摘要
                - suggestions: 建议列表
                - generated_at: 生成时间（可选）
            expire: 过期时间（秒），默认24小时（86400秒）
            
        Returns:
            是否设置成功
        """
        try:
            key = cls._get_key("suggestions", user_id)
            
            # 添加生成时间戳
            if "generated_at" not in suggestions_data:
                suggestions_data["generated_at"] = datetime.now().isoformat()
            
            # 添加缓存标记
            suggestions_data["cached"] = True
            
            value = json.dumps(suggestions_data, ensure_ascii=False)
            await aio_redis.set(key, value, ex=expire)
            logger.info(f"设置情绪建议缓存成功: {key}, 过期时间: {expire}秒")
            return True
        except Exception as e:
            logger.error(f"设置情绪建议缓存失败: {e}", exc_info=True)
            return False
    
    @classmethod
    async def get_emotion_suggestions(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户情绪建议缓存
        
        Args:
            user_id: 用户ID（end_user_id）
            
        Returns:
            建议数据字典，如果不存在或已过期返回 None
        """
        try:
            key = cls._get_key("suggestions", user_id)
            value = await aio_redis.get(key)
            
            if value:
                data = json.loads(value)
                logger.info(f"成功获取情绪建议缓存: {key}")
                return data
            
            logger.info(f"情绪建议缓存不存在或已过期: {key}")
            return None
        except Exception as e:
            logger.error(f"获取情绪建议缓存失败: {e}", exc_info=True)
            return None
    
    @classmethod
    async def delete_emotion_suggestions(cls, user_id: str) -> bool:
        """删除用户情绪建议缓存
        
        Args:
            user_id: 用户ID（end_user_id）
            
        Returns:
            是否删除成功
        """
        try:
            key = cls._get_key("suggestions", user_id)
            result = await aio_redis.delete(key)
            logger.info(f"删除情绪建议缓存: {key}, 结果: {result}")
            return result > 0
        except Exception as e:
            logger.error(f"删除情绪建议缓存失败: {e}", exc_info=True)
            return False
    
    @classmethod
    async def get_suggestions_ttl(cls, user_id: str) -> int:
        """获取情绪建议缓存的剩余过期时间
        
        Args:
            user_id: 用户ID（end_user_id）
            
        Returns:
            剩余秒数，-1表示永不过期，-2表示key不存在
        """
        try:
            key = cls._get_key("suggestions", user_id)
            ttl = await aio_redis.ttl(key)
            logger.debug(f"情绪建议缓存TTL: {key} = {ttl}秒")
            return ttl
        except Exception as e:
            logger.error(f"获取情绪建议缓存TTL失败: {e}")
            return -2
