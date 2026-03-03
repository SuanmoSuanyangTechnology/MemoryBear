"""
Implicit Emotions Storage Repository

数据访问层：处理隐性记忆和情绪数据的数据库操作
"""
import logging
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.implicit_emotions_storage_model import ImplicitEmotionsStorage

logger = logging.getLogger(__name__)


class ImplicitEmotionsStorageRepository:
    """隐性记忆和情绪存储仓储类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_by_end_user_id(self, end_user_id: str) -> Optional[ImplicitEmotionsStorage]:
        """根据终端用户ID获取存储记录
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            存储记录，如果不存在返回None
        """
        try:
            stmt = select(ImplicitEmotionsStorage).where(
                ImplicitEmotionsStorage.end_user_id == end_user_id
            )
            result = self.db.execute(stmt).scalar_one_or_none()
            return result
        except Exception as e:
            logger.error(f"获取用户存储记录失败: end_user_id={end_user_id}, error={e}")
            return None
    
    def create(self, end_user_id: str) -> ImplicitEmotionsStorage:
        """创建新的存储记录
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            新创建的存储记录
        """
        try:
            storage = ImplicitEmotionsStorage(
                end_user_id=end_user_id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            self.db.add(storage)
            self.db.commit()
            self.db.refresh(storage)
            logger.info(f"创建用户存储记录成功: end_user_id={end_user_id}")
            return storage
        except Exception as e:
            self.db.rollback()
            logger.error(f"创建用户存储记录失败: end_user_id={end_user_id}, error={e}")
            raise
    
    def update_implicit_profile(
        self,
        end_user_id: str,
        profile_data: dict
    ) -> Optional[ImplicitEmotionsStorage]:
        """更新隐性记忆画像数据
        
        Args:
            end_user_id: 终端用户ID
            profile_data: 画像数据
            
        Returns:
            更新后的存储记录
        """
        try:
            storage = self.get_by_end_user_id(end_user_id)
            
            if storage is None:
                # 如果记录不存在，创建新记录
                storage = self.create(end_user_id)
            
            storage.implicit_profile = profile_data
            storage.implicit_generated_at = datetime.utcnow()
            storage.updated_at = datetime.utcnow()
            
            self.db.commit()
            self.db.refresh(storage)
            logger.info(f"更新隐性记忆画像成功: end_user_id={end_user_id}")
            return storage
        except Exception as e:
            self.db.rollback()
            logger.error(f"更新隐性记忆画像失败: end_user_id={end_user_id}, error={e}")
            raise
    
    def update_emotion_suggestions(
        self,
        end_user_id: str,
        suggestions_data: dict
    ) -> Optional[ImplicitEmotionsStorage]:
        """更新情绪建议数据
        
        Args:
            end_user_id: 终端用户ID
            suggestions_data: 建议数据
            
        Returns:
            更新后的存储记录
        """
        try:
            storage = self.get_by_end_user_id(end_user_id)
            
            if storage is None:
                # 如果记录不存在，创建新记录
                storage = self.create(end_user_id)
            
            storage.emotion_suggestions = suggestions_data
            storage.emotion_generated_at = datetime.utcnow()
            storage.updated_at = datetime.utcnow()
            
            self.db.commit()
            self.db.refresh(storage)
            logger.info(f"更新情绪建议成功: end_user_id={end_user_id}")
            return storage
        except Exception as e:
            self.db.rollback()
            logger.error(f"更新情绪建议失败: end_user_id={end_user_id}, error={e}")
            raise
    
    def get_all_user_ids(self) -> List[str]:
        """获取所有已存储数据的用户ID列表
        
        Returns:
            用户ID列表
        """
        try:
            stmt = select(ImplicitEmotionsStorage.end_user_id)
            result = self.db.execute(stmt).scalars().all()
            return list(result)
        except Exception as e:
            logger.error(f"获取所有用户ID失败: error={e}")
            return []
    
    def delete_by_end_user_id(self, end_user_id: str) -> bool:
        """删除用户的存储记录
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            是否删除成功
        """
        try:
            storage = self.get_by_end_user_id(end_user_id)
            if storage:
                self.db.delete(storage)
                self.db.commit()
                logger.info(f"删除用户存储记录成功: end_user_id={end_user_id}")
                return True
            return False
        except Exception as e:
            self.db.rollback()
            logger.error(f"删除用户存储记录失败: end_user_id={end_user_id}, error={e}")
            return False
