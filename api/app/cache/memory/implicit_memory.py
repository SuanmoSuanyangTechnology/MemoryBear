"""
Implicit Memory Profile Cache

隐式记忆用户画像缓存模块
用于缓存用户的完整画像数据（偏好标签、四维画像、兴趣领域、行为习惯）
"""
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from app.dev_redis import dev_redis

logger = logging.getLogger(__name__)


class ImplicitMemoryCache:
    """隐式记忆用户画像缓存类"""
    
    # Key 前缀
    PREFIX = "cache:memory:implicit_memory"
    
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
    async def set_user_profile(
        cls,
        user_id: str,
        profile_data: Dict[str, Any],
        expire: int = 604800
    ) -> bool:
        """设置用户完整画像缓存
        
        Args:
            user_id: 用户ID（end_user_id）
            profile_data: 画像数据字典，包含：
                - preferences: 偏好标签列表
                - portrait: 四维画像对象
                - interest_areas: 兴趣领域分布对象
                - habits: 行为习惯列表
                - generated_at: 生成时间（可选）
            expire: 过期时间（秒），默认7天（604800秒）
            
        Returns:
            是否设置成功
        """
        try:
            key = cls._get_key("profile", user_id)
            
            # 添加生成时间戳
            if "generated_at" not in profile_data:
                profile_data["generated_at"] = datetime.now().isoformat()
            
            # 添加缓存标记
            profile_data["cached"] = True
            
            value = json.dumps(profile_data, ensure_ascii=False)
            await dev_redis.set(key, value, ex=expire)
            logger.info(f"设置用户画像缓存成功: {key}, 过期时间: {expire}秒")
            return True
        except Exception as e:
            logger.error(f"设置用户画像缓存失败: {e}", exc_info=True)
            return False
    
    @classmethod
    async def get_user_profile(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户完整画像缓存
        
        Args:
            user_id: 用户ID（end_user_id）
            
        Returns:
            画像数据字典，如果不存在或已过期返回 None
        """
        try:
            key = cls._get_key("profile", user_id)
            value = await dev_redis.get(key)
            
            if value:
                data = json.loads(value)
                logger.info(f"成功获取用户画像缓存: {key}")
                return data
            
            logger.info(f"用户画像缓存不存在或已过期: {key}")
            return None
        except Exception as e:
            logger.error(f"获取用户画像缓存失败: {e}", exc_info=True)
            return None
    
    @classmethod
    async def delete_user_profile(cls, user_id: str) -> bool:
        """删除用户完整画像缓存
        
        Args:
            user_id: 用户ID（end_user_id）
            
        Returns:
            是否删除成功
        """
        try:
            key = cls._get_key("profile", user_id)
            result = await dev_redis.delete(key)
            logger.info(f"删除用户画像缓存: {key}, 结果: {result}")
            return result > 0
        except Exception as e:
            logger.error(f"删除用户画像缓存失败: {e}", exc_info=True)
            return False
    
    @classmethod
    async def get_profile_ttl(cls, user_id: str) -> int:
        """获取用户画像缓存的剩余过期时间
        
        Args:
            user_id: 用户ID（end_user_id）
            
        Returns:
            剩余秒数，-1表示永不过期，-2表示key不存在
        """
        try:
            key = cls._get_key("profile", user_id)
            ttl = await dev_redis.ttl(key)
            logger.debug(f"用户画像缓存TTL: {key} = {ttl}秒")
            return ttl
        except Exception as e:
            logger.error(f"获取用户画像缓存TTL失败: {e}")
            return -2
