import datetime
import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.app_model import App
from app.models.end_user_model import EndUser
from app.models.workspace_model import Workspace

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

    def get_by_id(self, end_user_id: uuid.UUID) -> Optional[EndUser]:
        """根据ID获取终端用户（用于缓存操作）
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            Optional[EndUser]: 终端用户对象，如果不存在则返回None
        """
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id)
                .first()
            )
            if end_user:
                db_logger.debug(f"成功查询到终端用户 {end_user_id}")
            else:
                db_logger.debug(f"未找到终端用户 {end_user_id}")
            return end_user
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询终端用户 {end_user_id} 时出错: {str(e)}")
            raise

    def update_memory_insight(
        self, 
        end_user_id: uuid.UUID, 
        memory_insight: str,
        behavior_pattern: str,
        key_findings: str,
        growth_trajectory: str
    ) -> bool:
        """更新记忆洞察缓存（四个维度）
        
        Args:
            end_user_id: 终端用户ID
            memory_insight: 总体概述
            behavior_pattern: 行为模式
            key_findings: 关键发现
            growth_trajectory: 成长轨迹
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id)
                .update(
                    {
                        EndUser.memory_insight: memory_insight,  # 总体概述存储在 memory_insight
                        EndUser.behavior_pattern: behavior_pattern,
                        EndUser.key_findings: key_findings,
                        EndUser.growth_trajectory: growth_trajectory,
                        EndUser.memory_insight_updated_at: datetime.datetime.now()
                    },
                    synchronize_session=False
                )
            )
            
            self.db.commit()
            
            if updated_count > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的记忆洞察缓存（四维度）")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新记忆洞察缓存")
                return False
                
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的记忆洞察缓存时出错: {str(e)}")
            raise

    def update_user_summary(
        self, 
        end_user_id: uuid.UUID, 
        user_summary: str,
        personality: str,
        core_values: str,
        one_sentence: str
    ) -> bool:
        """更新用户摘要缓存（四个部分）
        
        Args:
            end_user_id: 终端用户ID
            user_summary: 基本介绍
            personality: 性格特点
            core_values: 核心价值观
            one_sentence: 一句话总结
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id)
                .update(
                    {
                        EndUser.user_summary: user_summary,  # 基本介绍存储在 user_summary
                        EndUser.personality_traits: personality,
                        EndUser.core_values: core_values,
                        EndUser.one_sentence_summary: one_sentence,
                        EndUser.user_summary_updated_at: datetime.datetime.now()
                    },
                    synchronize_session=False
                )
            )
            
            self.db.commit()
            
            if updated_count > 0:
                db_logger.info(f"成功更新终端用户 {end_user_id} 的用户摘要缓存（四部分）")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新用户摘要缓存")
                return False
                
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的用户摘要缓存时出错: {str(e)}")
            raise

    def get_all_by_workspace(self, workspace_id: uuid.UUID) -> List[EndUser]:
        """获取工作空间的所有终端用户
        
        Args:
            workspace_id: 工作空间ID
            
        Returns:
            List[EndUser]: 终端用户列表
        """
        try:
            end_users = (
                self.db.query(EndUser)
                .join(App, EndUser.app_id == App.id)
                .filter(App.workspace_id == workspace_id)
                .all()
            )
            db_logger.info(f"成功查询工作空间 {workspace_id} 下的 {len(end_users)} 个终端用户")
            return end_users
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询工作空间 {workspace_id} 下的终端用户时出错: {str(e)}")
            raise

    def get_all_active_workspaces(self) -> List[uuid.UUID]:
        """获取所有活动工作空间的ID
        
        Returns:
            List[uuid.UUID]: 活动工作空间ID列表
        """
        try:
            workspace_ids = (
                self.db.query(Workspace.id)
                .filter(Workspace.is_active)
                .all()
            )
            # 提取ID（查询返回的是元组列表）
            workspace_id_list = [workspace_id[0] for workspace_id in workspace_ids]
            db_logger.info(f"成功查询到 {len(workspace_id_list)} 个活动工作空间")
            return workspace_id_list
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"查询活动工作空间时出错: {str(e)}")
            raise

    def update_memory_config_id(self, end_user_id: uuid.UUID, memory_config_id: uuid.UUID) -> bool:
        """更新终端用户的 memory_config_id（懒更新）。
        
        Args:
            end_user_id: 终端用户ID
            memory_config_id: 记忆配置ID
            
        Returns:
            bool: 更新成功返回True，否则返回False
        """
        try:
            updated_count = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id)
                .update(
                    {EndUser.memory_config_id: memory_config_id},
                    synchronize_session=False
                )
            )
            self.db.commit()
            
            if updated_count > 0:
                db_logger.debug(f"成功更新终端用户 {end_user_id} 的 memory_config_id: {memory_config_id}")
                return True
            else:
                db_logger.warning(f"未找到终端用户 {end_user_id}，无法更新 memory_config_id")
                return False
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"更新终端用户 {end_user_id} 的 memory_config_id 时出错: {str(e)}")
            raise

    def get_memory_config_id(self, end_user_id: uuid.UUID) -> Optional[uuid.UUID]:
        """获取终端用户的 memory_config_id。
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            Optional[uuid.UUID]: memory_config_id 或 None
        """
        try:
            end_user = (
                self.db.query(EndUser)
                .filter(EndUser.id == end_user_id)
                .first()
            )
            if end_user and end_user.memory_config_id:
                db_logger.debug(f"获取终端用户 {end_user_id} 的 memory_config_id: {end_user.memory_config_id}")
                return end_user.memory_config_id
            return None
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"获取终端用户 {end_user_id} 的 memory_config_id 时出错: {str(e)}")
            raise

    def batch_update_memory_config_id(
        self,
        app_id: uuid.UUID,
        memory_config_id: uuid.UUID
    ) -> int:
        """批量更新应用下所有终端用户的 memory_config_id
        
        Args:
            app_id: 应用ID
            memory_config_id: 新的记忆配置ID
            
        Returns:
            int: 更新的行数
        """
        try:
            from sqlalchemy import update
            
            stmt = (
                update(EndUser)
                .where(EndUser.app_id == app_id)
                .values(memory_config_id=memory_config_id)
            )
            
            result = self.db.execute(stmt)
            self.db.commit()
            
            updated_count = result.rowcount
            
            db_logger.info(
                f"批量更新终端用户记忆配置: app_id={app_id}, "
                f"memory_config_id={memory_config_id}, updated_count={updated_count}"
            )
            
            return updated_count
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(
                f"批量更新终端用户记忆配置时出错: app_id={app_id}, "
                f"memory_config_id={memory_config_id}, error={str(e)}"
            )
            raise

    def count_by_memory_config_id(
        self,
        memory_config_id: uuid.UUID
    ) -> int:
        """统计使用指定记忆配置的终端用户数量
        
        Args:
            memory_config_id: 记忆配置ID
            
        Returns:
            int: 使用该配置的终端用户数量
        """
        try:
            from sqlalchemy import func, select
            
            stmt = (
                select(func.count(EndUser.id))
                .where(EndUser.memory_config_id == memory_config_id)
            )
            
            count = self.db.execute(stmt).scalar() or 0
            
            db_logger.debug(f"统计记忆配置使用数: memory_config_id={memory_config_id}, count={count}")
            
            return count
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(f"统计记忆配置使用数时出错: memory_config_id={memory_config_id}, error={str(e)}")
            raise

    def clear_memory_config_id(
        self,
        memory_config_id: uuid.UUID
    ) -> int:
        """清除所有使用指定记忆配置的终端用户的 memory_config_id
        
        将 memory_config_id 设置为 NULL
        
        Args:
            memory_config_id: 要清除的记忆配置ID
            
        Returns:
            int: 清除的行数
        """
        try:
            from sqlalchemy import update
            
            stmt = (
                update(EndUser)
                .where(EndUser.memory_config_id == memory_config_id)
                .values(memory_config_id=None)
            )
            
            result = self.db.execute(stmt)
            self.db.commit()
            
            cleared_count = result.rowcount
            
            db_logger.warning(
                f"清除终端用户记忆配置引用: memory_config_id={memory_config_id}, "
                f"cleared_count={cleared_count}"
            )
            
            return cleared_count
            
        except Exception as e:
            self.db.rollback()
            db_logger.error(
                f"清除终端用户记忆配置引用时出错: memory_config_id={memory_config_id}, "
                f"error={str(e)}"
            )
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

# 新增的缓存操作函数（保持与类方法一致的接口）
def get_by_id(db: Session, end_user_id: uuid.UUID) -> Optional[EndUser]:
    """根据ID获取终端用户（用于缓存操作）"""
    repo = EndUserRepository(db)
    return repo.get_by_id(end_user_id)

def update_memory_insight(
    db: Session, 
    end_user_id: uuid.UUID, 
    memory_insight: str,
    behavior_pattern: str,
    key_findings: str,
    growth_trajectory: str
) -> bool:
    """更新记忆洞察缓存（四个维度）"""
    repo = EndUserRepository(db)
    return repo.update_memory_insight(end_user_id, memory_insight, behavior_pattern, key_findings, growth_trajectory)

def update_user_summary(
    db: Session, 
    end_user_id: uuid.UUID, 
    user_summary: str,
    personality: str,
    core_values: str,
    one_sentence: str
) -> bool:
    """更新用户摘要缓存（四个部分）"""
    repo = EndUserRepository(db)
    return repo.update_user_summary(end_user_id, user_summary, personality, core_values, one_sentence)

def get_all_by_workspace(db: Session, workspace_id: uuid.UUID) -> List[EndUser]:
    """获取工作空间的所有终端用户"""
    repo = EndUserRepository(db)
    return repo.get_all_by_workspace(workspace_id)

def get_all_active_workspaces(db: Session) -> List[uuid.UUID]:
    """获取所有活动工作空间的ID"""
    repo = EndUserRepository(db)
    return repo.get_all_active_workspaces()


def update_memory_config_id(db: Session, end_user_id: uuid.UUID, memory_config_id: uuid.UUID) -> bool:
    """更新终端用户的 memory_config_id（懒更新）。
    
    Args:
        db: 数据库会话
        end_user_id: 终端用户ID
        memory_config_id: 记忆配置ID
        
    Returns:
        bool: 更新成功返回True，否则返回False
    """
    repo = EndUserRepository(db)
    return repo.update_memory_config_id(end_user_id, memory_config_id)


def get_memory_config_id(db: Session, end_user_id: uuid.UUID) -> Optional[uuid.UUID]:
    """获取终端用户的 memory_config_id。
    
    Args:
        db: 数据库会话
        end_user_id: 终端用户ID
        
    Returns:
        Optional[uuid.UUID]: memory_config_id 或 None
    """
    repo = EndUserRepository(db)
    return repo.get_memory_config_id(end_user_id)
