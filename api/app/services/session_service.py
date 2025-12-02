from typing import Optional
import json
from datetime import datetime, timedelta, timezone

from app.aioRedis import aio_redis_set, aio_redis_get, aio_redis_delete
from app.core.config import settings


class SessionService:
    """用户会话管理服务"""
    
    @staticmethod
    def _get_user_session_key(username: str) -> str:
        """获取用户会话的Redis键"""
        return f"user_session:{username}"
    
    @staticmethod
    def _get_token_blacklist_key(token_id: str) -> str:
        """获取token黑名单的Redis键"""
        return f"token_blacklist:{token_id}"
    
    @staticmethod
    async def set_user_active_session(username: str, token_id: str, expires_at: datetime) -> None:
        """设置用户的活跃会话
        
        Args:
            username: 用户名
            token_id: token的唯一标识
            expires_at: token过期时间
        """
        if not settings.ENABLE_SINGLE_SESSION:
            return
            
        session_key = SessionService._get_user_session_key(username)
        session_data = {
            "token_id": token_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat()
        }
        
        # 计算过期时间（秒）
        expire_seconds = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if expire_seconds > 0:
            await aio_redis_set(session_key, session_data, expire=expire_seconds)
    
    @staticmethod
    async def get_user_active_session(username: str) -> Optional[dict]:
        """获取用户的活跃会话
        
        Args:
            username: 用户名
            
        Returns:
            会话数据字典或None
        """
        if not settings.ENABLE_SINGLE_SESSION:
            return None
            
        session_key = SessionService._get_user_session_key(username)
        session_data = await aio_redis_get(session_key)
        
        if session_data:
            try:
                return json.loads(session_data) if isinstance(session_data, str) else session_data
            except json.JSONDecodeError:
                return None
        return None
    
    @staticmethod
    async def invalidate_old_session(username: str, new_token_id: str) -> None:
        """使旧会话失效
        
        Args:
            username: 用户名
            new_token_id: 新token的ID
        """
        if not settings.ENABLE_SINGLE_SESSION:
            return
            
        # 获取当前活跃会话
        current_session = await SessionService.get_user_active_session(username)
        
        if current_session and current_session.get("token_id") != new_token_id:
            # 将旧token加入黑名单
            old_token_id = current_session.get("token_id")
            if old_token_id:
                await SessionService.blacklist_token(old_token_id)
    
    @staticmethod
    async def blacklist_token(token_id: str, expire_seconds: int = None) -> None:
        """将token加入黑名单
        
        Args:
            token_id: token的唯一标识
            expire_seconds: 黑名单过期时间（秒），默认为refresh token的过期时间
        """
        if expire_seconds is None:
            # 默认使用refresh token的过期时间
            expire_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
            
        blacklist_key = SessionService._get_token_blacklist_key(token_id)
        await aio_redis_set(blacklist_key, "blacklisted", expire=expire_seconds)
    
    @staticmethod
    async def is_token_blacklisted(token_id: str) -> bool:
        """检查token是否在黑名单中
        
        Args:
            token_id: token的唯一标识
            
        Returns:
            True如果token在黑名单中，否则False
        """
        if not settings.ENABLE_SINGLE_SESSION:
            return False
            
        blacklist_key = SessionService._get_token_blacklist_key(token_id)
        result = await aio_redis_get(blacklist_key)
        return result is not None
    
    @staticmethod
    async def clear_user_session(username: str) -> None:
        """清除用户会话
        
        Args:
            username: 用户名
        """
        session_key = SessionService._get_user_session_key(username)
        await aio_redis_delete(session_key)
    
    @staticmethod
    async def invalidate_all_user_tokens(user_id: str) -> None:
        """使用户的所有 tokens 失效（用于密码重置等场景）
        
        通过在 Redis 中设置一个用户级别的失效标记来实现。
        所有在此时间点之前签发的 tokens 都将被视为无效。
        
        Args:
            user_id: 用户ID
        """
        invalidation_key = f"user_token_invalidation:{user_id}"
        current_time = datetime.now(timezone.utc).isoformat()
        
        # 设置失效时间戳，过期时间为 refresh token 的最大有效期
        expire_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        await aio_redis_set(invalidation_key, current_time, expire=expire_seconds)
    
    @staticmethod
    async def get_user_token_invalidation_time(user_id: str) -> Optional[str]:
        """获取用户 token 失效时间
        
        Args:
            user_id: 用户ID
            
        Returns:
            失效时间的 ISO 格式字符串，如果没有失效记录则返回 None
        """
        invalidation_key = f"user_token_invalidation:{user_id}"
        result = await aio_redis_get(invalidation_key)
        return result if result else None