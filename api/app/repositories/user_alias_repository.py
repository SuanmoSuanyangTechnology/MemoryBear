"""
用户别名仓储层
"""
import uuid
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.user_alias_model import UserAlias
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class UserAliasRepository:
    """用户别名仓储类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, end_user_id: uuid.UUID, other_name: str, alias: str = None, meta_data: dict = None) -> UserAlias:
        """创建用户别名"""
        user_alias = UserAlias(
            end_user_id=end_user_id,
            other_name=other_name,
            alias=alias,
            meta_data=meta_data
        )
        self.db.add(user_alias)
        self.db.commit()
        self.db.refresh(user_alias)
        logger.info(f"创建用户别名: end_user_id={end_user_id}, alias={alias}")
        return user_alias
    
    def get_by_id(self, alias_id: uuid.UUID) -> Optional[UserAlias]:
        """根据ID获取别名"""
        return self.db.query(UserAlias).filter(UserAlias.id == alias_id).first()
    
    def get_by_end_user_id(self, end_user_id: uuid.UUID) -> List[UserAlias]:
        """获取用户的所有别名"""
        return self.db.query(UserAlias).filter(UserAlias.end_user_id == end_user_id).all()
    
    def update(self, alias_id: uuid.UUID, alias: str = None, meta_data: dict = None) -> Optional[UserAlias]:
        """更新别名"""
        user_alias = self.get_by_id(alias_id)
        if user_alias:
            if alias is not None:
                user_alias.alias = alias
            if meta_data is not None:
                user_alias.meta_data = meta_data
            self.db.commit()
            self.db.refresh(user_alias)
            logger.info(f"更新用户别名: alias_id={alias_id}")
        return user_alias
    
    def delete(self, alias_id: uuid.UUID) -> bool:
        """删除别名"""
        user_alias = self.get_by_id(alias_id)
        if user_alias:
            self.db.delete(user_alias)
            self.db.commit()
            logger.info(f"删除用户别名: alias_id={alias_id}")
            return True
        return False
    
    def delete_by_end_user_id(self, end_user_id: uuid.UUID) -> int:
        """删除用户的所有别名"""
        count = self.db.query(UserAlias).filter(UserAlias.end_user_id == end_user_id).delete()
        self.db.commit()
        logger.info(f"删除用户所有别名: end_user_id={end_user_id}, count={count}")
        return count
    
    def batch_create(self, end_user_id: uuid.UUID, other_name: str, aliases: List[str]) -> List[UserAlias]:
        """批量创建别名"""
        user_aliases = []
        for alias in aliases:
            if alias and alias.strip():
                user_alias = UserAlias(
                    end_user_id=end_user_id,
                    other_name=other_name,
                    alias=alias.strip()
                )
                self.db.add(user_alias)
                user_aliases.append(user_alias)
        
        self.db.commit()
        for user_alias in user_aliases:
            self.db.refresh(user_alias)
        
        logger.info(f"批量创建用户别名: end_user_id={end_user_id}, count={len(user_aliases)}")
        return user_aliases
