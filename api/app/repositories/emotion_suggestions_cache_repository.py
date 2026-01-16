"""情绪建议缓存仓储层"""

from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import datetime

from app.models.emotion_suggestions_cache_model import EmotionSuggestionsCache
from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class EmotionSuggestionsCacheRepository:
    """情绪建议缓存仓储类"""
    
    def __init__(self, db: Session):
        self.db = db

    def get_by_end_user_id(self, end_user_id: str) -> Optional[EmotionSuggestionsCache]:
        """根据终端用户ID获取缓存
        
        Args:
            end_user_id: 终端用户ID（组ID）
            
        Returns:
            缓存记录，如果不存在返回 None
        """
        try:
            cache = (
                self.db.query(EmotionSuggestionsCache)
                .filter(EmotionSuggestionsCache.end_user_id == end_user_id)
                .first()
            )
            if cache:
                db_logger.info(f"成功获取用户 {end_user_id} 的情绪建议缓存")
            else:
                db_logger.info(f"用户 {end_user_id} 的情绪建议缓存不存在")
            return cache
        except Exception as e:
            db_logger.error(f"获取用户 {end_user_id} 的情绪建议缓存失败: {str(e)}")
            raise

    def create_or_update(
        self,
        end_user_id: str,
        health_summary: str,
        suggestions: list,
        expires_hours: int = 24
    ) -> EmotionSuggestionsCache:
        """创建或更新缓存
        
        Args:
            end_user_id: 终端用户ID（组ID）
            health_summary: 健康状态摘要
            suggestions: 建议列表
            expires_hours: 过期时间（小时），默认24小时
            
        Returns:
            缓存记录
        """
        try:
            # 查找现有记录
            cache = self.get_by_end_user_id(end_user_id)
            
            now = datetime.datetime.now()
            expires_at = now + datetime.timedelta(hours=expires_hours)
            
            if cache:
                # 更新现有记录
                cache.health_summary = health_summary
                cache.suggestions = suggestions
                cache.generated_at = now
                cache.expires_at = expires_at
                cache.updated_at = now
                db_logger.info(f"更新用户 {end_user_id} 的情绪建议缓存")
            else:
                # 创建新记录
                cache = EmotionSuggestionsCache(
                    end_user_id=end_user_id,
                    health_summary=health_summary,
                    suggestions=suggestions,
                    generated_at=now,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now
                )
                self.db.add(cache)
                db_logger.info(f"创建用户 {end_user_id} 的情绪建议缓存")
            
            self.db.commit()
            self.db.refresh(cache)
            return cache
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"创建或更新用户 {end_user_id} 的情绪建议缓存失败: {str(e)}")
            raise

    def delete_by_end_user_id(self, end_user_id: str) -> bool:
        """删除缓存
        
        Args:
            end_user_id: 终端用户ID（组ID）
            
        Returns:
            是否删除成功
        """
        try:
            cache = self.get_by_end_user_id(end_user_id)
            if cache:
                self.db.delete(cache)
                self.db.commit()
                db_logger.info(f"删除用户 {end_user_id} 的情绪建议缓存")
                return True
            return False
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"删除用户 {end_user_id} 的情绪建议缓存失败: {str(e)}")
            raise

    @staticmethod
    def is_expired(cache: EmotionSuggestionsCache) -> bool:
        """检查缓存是否过期
        
        Args:
            cache: 缓存记录
            
        Returns:
            是否过期
        """
        if cache.expires_at is None:
            return False
        return datetime.datetime.now() > cache.expires_at


# 便捷函数
def get_cache_by_end_user_id(db: Session, end_user_id: str) -> Optional[EmotionSuggestionsCache]:
    """根据终端用户ID获取缓存"""
    repo = EmotionSuggestionsCacheRepository(db)
    return repo.get_by_end_user_id(end_user_id)


def create_or_update_cache(
    db: Session,
    end_user_id: str,
    health_summary: str,
    suggestions: list,
    expires_hours: int = 24
) -> EmotionSuggestionsCache:
    """创建或更新缓存"""
    repo = EmotionSuggestionsCacheRepository(db)
    return repo.create_or_update(end_user_id, health_summary, suggestions, expires_hours)


def delete_cache_by_end_user_id(db: Session, end_user_id: str) -> bool:
    """删除缓存"""
    repo = EmotionSuggestionsCacheRepository(db)
    return repo.delete_by_end_user_id(end_user_id)


def is_cache_expired(cache: EmotionSuggestionsCache) -> bool:
    """检查缓存是否过期"""
    return EmotionSuggestionsCacheRepository.is_expired(cache)
