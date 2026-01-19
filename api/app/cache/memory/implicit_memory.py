"""
Implicit Memory Cache

隐式记忆缓存模块
用于缓存用户的隐式记忆、行为模式、偏好等数据
"""
import json
import logging
from typing import Optional, Dict, Any, List, Set
from datetime import datetime

from app.dev_redis import dev_redis

logger = logging.getLogger(__name__)


class ImplicitMemoryCache:
    """隐式记忆缓存类"""
    
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
    async def set_user_behavior(
        cls,
        user_id: str,
        behavior_data: Dict[str, Any],
        expire: int = 7200
    ) -> bool:
        """设置用户行为数据
        
        Args:
            user_id: 用户ID
            behavior_data: 行为数据字典
            expire: 过期时间（秒），默认2小时
            
        Returns:
            是否设置成功
        """
        try:
            key = cls._get_key("behavior", user_id)
            value = json.dumps(behavior_data, ensure_ascii=False)
            await dev_redis.set(key, value, ex=expire)
            logger.debug(f"设置用户行为缓存: {key}")
            return True
        except Exception as e:
            logger.error(f"设置用户行为缓存失败: {e}")
            return False
    
    @classmethod
    async def get_user_behavior(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户行为数据
        
        Args:
            user_id: 用户ID
            
        Returns:
            行为数据字典，如果不存在返回 None
        """
        try:
            key = cls._get_key("behavior", user_id)
            value = await dev_redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"获取用户行为缓存失败: {e}")
            return None
    
    @classmethod
    async def set_user_preferences(
        cls,
        user_id: str,
        preferences: Dict[str, Any],
        expire: int = 86400
    ) -> bool:
        """设置用户偏好数据（使用 Redis Hash）
        
        Args:
            user_id: 用户ID
            preferences: 偏好数据字典
            expire: 过期时间（秒），默认24小时
            
        Returns:
            是否设置成功
        """
        try:
            key = cls._get_key("preferences", user_id)
            
            # 将字典转换为 Redis Hash 格式
            hash_data = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) 
                        for k, v in preferences.items()}
            
            await dev_redis.hset(key, mapping=hash_data)
            await dev_redis.expire(key, expire)
            
            logger.debug(f"设置用户偏好缓存: {key}")
            return True
        except Exception as e:
            logger.error(f"设置用户偏好缓存失败: {e}")
            return False
    
    @classmethod
    async def get_user_preferences(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户偏好数据
        
        Args:
            user_id: 用户ID
            
        Returns:
            偏好数据字典，如果不存在返回 None
        """
        try:
            key = cls._get_key("preferences", user_id)
            hash_data = await dev_redis.hgetall(key)
            
            if not hash_data:
                return None
            
            # 尝试解析 JSON 值
            result = {}
            for k, v in hash_data.items():
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            
            return result
        except Exception as e:
            logger.error(f"获取用户偏好缓存失败: {e}")
            return None
    
    @classmethod
    async def update_preference(
        cls,
        user_id: str,
        preference_key: str,
        preference_value: Any
    ) -> bool:
        """更新单个偏好项
        
        Args:
            user_id: 用户ID
            preference_key: 偏好键
            preference_value: 偏好值
            
        Returns:
            是否更新成功
        """
        try:
            key = cls._get_key("preferences", user_id)
            value = json.dumps(preference_value) if isinstance(preference_value, (dict, list)) else str(preference_value)
            await dev_redis.hset(key, preference_key, value)
            logger.debug(f"更新用户偏好: {key}.{preference_key}")
            return True
        except Exception as e:
            logger.error(f"更新用户偏好失败: {e}")
            return False
    
    @classmethod
    async def add_behavior_pattern(
        cls,
        user_id: str,
        pattern: Dict[str, Any],
        max_patterns: int = 50
    ) -> bool:
        """添加行为模式记录（使用 Redis List）
        
        Args:
            user_id: 用户ID
            pattern: 行为模式数据
            max_patterns: 最大模式数量
            
        Returns:
            是否添加成功
        """
        try:
            key = cls._get_key("patterns", user_id)
            
            # 添加时间戳
            pattern["timestamp"] = datetime.now().isoformat()
            pattern_json = json.dumps(pattern, ensure_ascii=False)
            
            await dev_redis.lpush(key, pattern_json)
            await dev_redis.ltrim(key, 0, max_patterns - 1)
            
            # 设置过期时间（30天）
            await dev_redis.expire(key, 30 * 24 * 3600)
            
            logger.debug(f"添加行为模式: {key}")
            return True
        except Exception as e:
            logger.error(f"添加行为模式失败: {e}")
            return False
    
    @classmethod
    async def get_behavior_patterns(
        cls,
        user_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """获取用户行为模式列表
        
        Args:
            user_id: 用户ID
            limit: 返回模式数量
            
        Returns:
            行为模式列表
        """
        try:
            key = cls._get_key("patterns", user_id)
            patterns = await dev_redis.lrange(key, 0, limit - 1)
            return [json.loads(pattern) for pattern in patterns]
        except Exception as e:
            logger.error(f"获取行为模式失败: {e}")
            return []
    
    @classmethod
    async def add_interaction_tag(
        cls,
        user_id: str,
        tag: str
    ) -> bool:
        """添加交互标签（使用 Redis Set）
        
        Args:
            user_id: 用户ID
            tag: 标签名称
            
        Returns:
            是否添加成功
        """
        try:
            key = cls._get_key("tags", user_id)
            await dev_redis.sadd(key, tag)
            
            # 设置过期时间（30天）
            await dev_redis.expire(key, 30 * 24 * 3600)
            
            logger.debug(f"添加交互标签: {key} - {tag}")
            return True
        except Exception as e:
            logger.error(f"添加交互标签失败: {e}")
            return False
    
    @classmethod
    async def get_interaction_tags(cls, user_id: str) -> Set[str]:
        """获取用户所有交互标签
        
        Args:
            user_id: 用户ID
            
        Returns:
            标签集合
        """
        try:
            key = cls._get_key("tags", user_id)
            tags = await dev_redis.smembers(key)
            return set(tags) if tags else set()
        except Exception as e:
            logger.error(f"获取交互标签失败: {e}")
            return set()
    
    @classmethod
    async def remove_interaction_tag(cls, user_id: str, tag: str) -> bool:
        """移除交互标签
        
        Args:
            user_id: 用户ID
            tag: 标签名称
            
        Returns:
            是否移除成功
        """
        try:
            key = cls._get_key("tags", user_id)
            await dev_redis.srem(key, tag)
            logger.debug(f"移除交互标签: {key} - {tag}")
            return True
        except Exception as e:
            logger.error(f"移除交互标签失败: {e}")
            return False
    
    @classmethod
    async def increment_action_counter(
        cls,
        user_id: str,
        action_type: str,
        increment: int = 1
    ) -> int:
        """增加行为动作计数器
        
        Args:
            user_id: 用户ID
            action_type: 动作类型（如 click, view, search 等）
            increment: 增加的数量
            
        Returns:
            增加后的计数值
        """
        try:
            key = cls._get_key("action_counter", user_id, action_type)
            count = await dev_redis.incrby(key, increment)
            
            # 设置过期时间（30天）
            await dev_redis.expire(key, 30 * 24 * 3600)
            
            logger.debug(f"行为计数器增加: {key} = {count}")
            return count
        except Exception as e:
            logger.error(f"增加行为计数器失败: {e}")
            return 0
    
    @classmethod
    async def get_action_counter(cls, user_id: str, action_type: str) -> int:
        """获取行为动作计数
        
        Args:
            user_id: 用户ID
            action_type: 动作类型
            
        Returns:
            计数值
        """
        try:
            key = cls._get_key("action_counter", user_id, action_type)
            count = await dev_redis.get(key)
            return int(count) if count else 0
        except Exception as e:
            logger.error(f"获取行为计数器失败: {e}")
            return 0
    
    @classmethod
    async def set_learning_model(
        cls,
        user_id: str,
        model_data: Dict[str, Any],
        expire: int = 86400
    ) -> bool:
        """设置用户学习模型数据
        
        Args:
            user_id: 用户ID
            model_data: 模型数据
            expire: 过期时间（秒），默认24小时
            
        Returns:
            是否设置成功
        """
        try:
            key = cls._get_key("learning_model", user_id)
            value = json.dumps(model_data, ensure_ascii=False)
            await dev_redis.set(key, value, ex=expire)
            logger.debug(f"设置学习模型缓存: {key}")
            return True
        except Exception as e:
            logger.error(f"设置学习模型缓存失败: {e}")
            return False
    
    @classmethod
    async def get_learning_model(cls, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户学习模型数据
        
        Args:
            user_id: 用户ID
            
        Returns:
            模型数据字典，如果不存在返回 None
        """
        try:
            key = cls._get_key("learning_model", user_id)
            value = await dev_redis.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"获取学习模型缓存失败: {e}")
            return None
    
    @classmethod
    async def clear_user_cache(cls, user_id: str) -> int:
        """清除用户所有隐式记忆缓存
        
        Args:
            user_id: 用户ID
            
        Returns:
            删除的 key 数量
        """
        try:
            pattern = cls._get_key("*", user_id, "*")
            keys = await dev_redis.keys(pattern)
            
            if keys:
                deleted = await dev_redis.delete(*keys)
                logger.info(f"清除用户隐式记忆缓存: {user_id}, 删除 {deleted} 个key")
                return deleted
            return 0
        except Exception as e:
            logger.error(f"清除用户隐式记忆缓存失败: {e}")
            return 0
    
    @classmethod
    async def get_all_cached_users(cls) -> List[str]:
        """获取所有有缓存的用户ID列表
        
        Returns:
            用户ID列表
        """
        try:
            pattern = cls._get_key("*", "*")
            keys = await dev_redis.keys(pattern)
            
            # 从 key 中提取用户ID
            user_ids = []
            for key in keys:
                parts = key.split(":")
                if len(parts) >= 4:
                    user_ids.append(parts[3])
            
            return list(set(user_ids))
        except Exception as e:
            logger.error(f"获取缓存用户列表失败: {e}")
            return []
    
    @classmethod
    async def get_cache_stats(cls) -> Dict[str, int]:
        """获取缓存统计信息
        
        Returns:
            统计信息字典
        """
        try:
            pattern = cls._get_key("*")
            all_keys = await dev_redis.keys(pattern)
            
            stats = {
                "total_keys": len(all_keys),
                "behavior_keys": len([k for k in all_keys if ":behavior:" in k]),
                "preferences_keys": len([k for k in all_keys if ":preferences:" in k]),
                "patterns_keys": len([k for k in all_keys if ":patterns:" in k]),
                "tags_keys": len([k for k in all_keys if ":tags:" in k]),
                "action_counter_keys": len([k for k in all_keys if ":action_counter:" in k]),
                "learning_model_keys": len([k for k in all_keys if ":learning_model:" in k]),
            }
            
            return stats
        except Exception as e:
            logger.error(f"获取缓存统计失败: {e}")
            return {}
