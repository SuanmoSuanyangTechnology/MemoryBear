from sqlalchemy.orm import Session
from typing import List, Optional
import uuid

from app.models.end_user_model import EndUser

from app.core.logging_config import get_db_logger

# 获取数据库专用日志器
db_logger = get_db_logger()


class EndUserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_end_users_by_app_id(self, app_id: uuid.UUID) -> List[EndUser]:
        """根据应用ID查询宿主"""
        try:
            end_users = (
                self.db.query(EndUser)
                .filter(EndUser.app_id == app_id)
                .all()
            )
            db_logger.info(f"成功查询应用 {app_id} 下的 {len(end_users)} 个宿主")
            return end_users
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询应用 {app_id} 下宿主时出错: {str(e)}")
            raise

    def get_end_user_by_id(self, end_user_id: uuid.UUID) -> Optional[EndUser]:
        """根据 end_user_id 查询宿主"""
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id)
                .first()
            )
            if end_user:
                db_logger.info(f"成功查询到宿主 {end_user_id}")
            else:
                db_logger.info(f"未找到宿主 {end_user_id}")
            return end_user
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询宿主 {end_user_id} 时出错: {str(e)}")
            raise

    def get_or_create_end_user(
        self, 
        app_id: uuid.UUID, 
        other_id: str,
        original_user_id: Optional[str] = None
    ) -> EndUser:
        """获取或创建终端用户
        
        Args:
            app_id: 应用ID
            other_id: 第三方ID
            original_user_id: 原始用户ID (存储到 other_id)
        """
        try:
            # 尝试查找现有用户
            end_user = (
                self.db.query(EndUser)
                .filter(
                    EndUser.app_id == app_id,
                    EndUser.other_id == other_id
                )
                .first()
            )
            
            if end_user:
                db_logger.debug(f"找到现有终端用户: 应用ID {app_id}、第三方ID {other_id}")
                return end_user
            
            # 创建新用户
            end_user = EndUser(
                app_id=app_id,
                other_id=other_id
            )
            self.db.add(end_user)
            self.db.commit()
            self.db.refresh(end_user)
            
            db_logger.info(f"创建新终端用户: (other_id: {other_id}) for app {app_id}")
            return end_user
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"获取或创建终端用户时出错: {str(e)}")
            raise

def get_end_users_by_app_id(db: Session, app_id: uuid.UUID) -> List[EndUser]:
    """根据应用ID查询宿主（返回 EndUser ORM 列表）"""
    repo = EndUserRepository(db)
    end_users = repo.get_end_users_by_app_id(app_id)
    return end_users

def get_end_user_by_id(db: Session, end_user_id: uuid.UUID) -> Optional[EndUser]:
    """根据 end_user_id 查询对应宿主"""
    repo = EndUserRepository(db)
    end_user = repo.get_end_user_by_id(end_user_id)
    return end_user

def update_end_user_other_name(
    db: Session, 
    end_user_id: uuid.UUID,
    other_name: str
) -> int:
    """
    通过 end_user_id 更新 end_user 表中的 other_name 字段
    
    Args:
        db: 数据库会话
        end_user_id: 宿主ID
        other_name: 要更新的用户名
        
    Returns:
        int: 更新的记录数
    """
    try:
        # 执行更新
        updated_count = (
            db.query(EndUser)
            .filter(EndUser.id == end_user_id)
            .update(
                {EndUser.other_name: other_name},
                synchronize_session=False
            )
        )
        
        db.commit()
        db_logger.info(f"成功更新宿主 {end_user_id} 的 other_name 为: {other_name}")
        return updated_count
        
    except Exception as e:
        db.rollback()
        db_logger.error(f"更新宿主 {end_user_id} 的 other_name 时出错: {str(e)}")
        raise