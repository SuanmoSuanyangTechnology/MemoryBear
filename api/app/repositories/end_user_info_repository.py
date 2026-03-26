"""
终端用户信息仓储层
"""
import uuid
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.end_user_info_model import EndUserInfo
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class EndUserInfoRepository:
    """终端用户信息仓储类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, end_user_id: uuid.UUID, other_name: str, aliases: List[str] = None, meta_data: dict = None) -> EndUserInfo:
        """创建终端用户信息"""
        end_user_info = EndUserInfo(
            end_user_id=end_user_id,
            other_name=other_name,
            aliases=aliases or [],
            meta_data=meta_data
        )
        self.db.add(end_user_info)
        self.db.commit()
        self.db.refresh(end_user_info)
        logger.info(f"创建终端用户信息: end_user_id={end_user_id}, aliases={aliases}")
        return end_user_info
    
    def get_by_id(self, info_id: uuid.UUID) -> Optional[EndUserInfo]:
        """根据ID获取用户信息"""
        return self.db.query(EndUserInfo).filter(EndUserInfo.id == info_id).first()
    

    def get_by_end_user_id(self, end_user_id: uuid.UUID) -> Optional[EndUserInfo]:
        """获取用户的信息记录"""
        return self.db.query(EndUserInfo).filter(EndUserInfo.end_user_id == end_user_id).first()
    
    def update(self, info_id: uuid.UUID, aliases: List[str] = None, meta_data: dict = None) -> Optional[EndUserInfo]:
        """更新用户信息"""
        end_user_info = self.get_by_id(info_id)
        if end_user_info:
            if aliases is not None:
                end_user_info.aliases = aliases
            if meta_data is not None:
                end_user_info.meta_data = meta_data
            self.db.commit()
            self.db.refresh(end_user_info)
            logger.info(f"更新终端用户信息: info_id={info_id}")
        return end_user_info
    
    def delete(self, info_id: uuid.UUID) -> bool:
        """删除用户信息"""
        end_user_info = self.get_by_id(info_id)
        if end_user_info:
            self.db.delete(end_user_info)
            self.db.commit()
            logger.info(f"删除终端用户信息: info_id={info_id}")
            return True
        return False
    
    def delete_by_end_user_id(self, end_user_id: uuid.UUID) -> int:
        """删除用户的所有信息记录"""
        count = self.db.query(EndUserInfo).filter(EndUserInfo.end_user_id == end_user_id).delete()
        self.db.commit()
        logger.info(f"删除用户所有信息记录: end_user_id={end_user_id}, count={count}")
        return count
