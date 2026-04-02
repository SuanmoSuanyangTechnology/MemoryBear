from datetime import datetime, time
from sqlalchemy.orm import Session
from sqlalchemy import func, Table, MetaData
from uuid import UUID
from typing import Dict, Optional, Any

from app.models.end_user_model import EndUser
from app.models.user_model import User
from app.models.workspace_model import Workspace, WorkspaceMember
from app.models.models_model import ModelConfig
from app.models.app_model import App

class HomePageRepository:
    
    @staticmethod
    def get_model_statistics(db: Session, tenant_id: UUID, week_start: datetime) -> tuple[int, int, int, float]:
        """获取模型统计数据"""
        total_models = db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active.is_(True)
        ).count()

        total_llm = db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active.is_(True),
            ModelConfig.type == "llm"
        ).count()

        total_embedding = db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active.is_(True),
            ModelConfig.type == "embedding"
        ).count()
        
        new_models_this_week = db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active.is_(True),
            ModelConfig.created_at >= week_start
        ).count()

        if total_models == 0:
            growth_rate = 0.0
        elif new_models_this_week == 0:
            growth_rate = 0.0
        else:
            last_week_total = total_models - new_models_this_week
            if last_week_total == 0:
                growth_rate = 100.0
            else:
                growth_rate = round((new_models_this_week / last_week_total) * 100, 2)
        
        return total_models, total_llm, total_embedding, growth_rate
    
    @staticmethod
    def get_workspace_statistics(db: Session, tenant_id: UUID, week_start: datetime) -> tuple[int, int, float]:
        """获取工作空间统计数据"""
        active_workspaces = db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True)
        ).count()
        
        new_workspaces_this_week = db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True),
            Workspace.created_at >= week_start
        ).count()

        if active_workspaces == 0:
            growth_rate = 0.0
        elif new_workspaces_this_week == 0:
            growth_rate = 0.0
        else:
            last_week_workspaces = active_workspaces - new_workspaces_this_week
            if last_week_workspaces == 0:
                growth_rate = 100.0
            else:
                growth_rate = round((new_workspaces_this_week / last_week_workspaces) * 100, 2)
        
        return active_workspaces, new_workspaces_this_week, growth_rate
    
    @staticmethod
    def get_user_statistics(db: Session, tenant_id: UUID, week_start: datetime) -> tuple[int, int, float]:
        """获取用户统计数据"""
        workspace_ids = db.query(Workspace.id).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True)
        ).subquery()

        total_users = db.query(EndUser).join(
            App,
            EndUser.app_id == App.id
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active.is_(True),
            App.status == "active"
        ).count()

        new_users_this_week = db.query(EndUser).join(
            App,
            EndUser.app_id == App.id
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active.is_(True),
            App.status == "active",
            EndUser.created_at >= week_start
        ).count()

        if total_users == 0:
            growth_rate = 0.0
        elif new_users_this_week == 0:
            growth_rate = 0.0
        else:
            last_week_users = total_users - new_users_this_week
            if last_week_users == 0:
                growth_rate = 100.0
            else:
                growth_rate = round((new_users_this_week / last_week_users) * 100, 2)
        
        return total_users, new_users_this_week, growth_rate
    
    @staticmethod
    def get_app_statistics(db: Session, tenant_id: UUID, week_start: datetime) -> tuple[int, int, float]:
        """获取应用统计数据"""
        workspace_ids = db.query(Workspace.id).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True)
        ).subquery()
        
        running_apps = db.query(App).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active.is_(True),
            App.status == "active"
        ).count()
        
        new_apps_this_week = db.query(App).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active.is_(True),
            App.status == "active",
            App.created_at >= week_start
        ).count()

        if running_apps == 0:
            growth_rate = 0.0
        elif new_apps_this_week == 0:
            growth_rate = 0.0
        else:
            last_week_apps = running_apps - new_apps_this_week
            if last_week_apps == 0:
                growth_rate = 100.0
            else:
                growth_rate = round((new_apps_this_week / last_week_apps) * 100, 2)
        
        return running_apps, new_apps_this_week, growth_rate
    
    @staticmethod
    def get_workspaces_with_counts(db: Session, tenant_id: UUID) -> tuple[list[Workspace], Dict[UUID, int], Dict[UUID, int]]:
        """批量获取工作空间及其统计数据"""
        # 获取工作空间列表
        workspaces = db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True)
        ).all()

        workspace_ids = [ws.id for ws in workspaces]
        
        # 批量获取应用数量
        app_counts = db.query(
            App.workspace_id,
            func.count(App.id).label('count')
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active.is_(True),
            App.status == "active"
        ).group_by(App.workspace_id).all()
        
        app_count_dict = {workspace_id: count for workspace_id, count in app_counts}
        
        # 批量获取用户数量
        user_counts = db.query(
            App.workspace_id,
            func.count(EndUser.id).label('count')
        ).join(
            EndUser,
            EndUser.app_id == App.id
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active.is_(True),
            App.status == "active"
        ).group_by(App.workspace_id).all()
        
        user_count_dict = {workspace_id: count for workspace_id, count in user_counts}
        
        return workspaces, app_count_dict, user_count_dict
    
    @staticmethod
    def get_latest_version_introduction(db: Session) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """
        从数据库获取最新已发布的版本说明
        使用反射方式读取表结构，不依赖 premium 模型类
        
        Args:
            db: 数据库会话
            
        Returns:
            (版本号，版本说明字典) 的元组
            如果数据库中没有已发布的版本，返回 (None, None)
        """
        try:
            metadata = MetaData()
            
            version_notes = Table('version_notes', metadata, autoload_with=db.bind)
            
            # 获取最新已发布的版本（按发布时间倒序，日期相同时按版本号倒序）
            query = db.query(version_notes).filter(
                version_notes.c.is_published == True
            ).order_by(
                version_notes.c.release_date.desc(),
                version_notes.c.version.desc()
            )
            
            note = query.first()
            
            if not note:
                return None, None
            
            version_info = {
                "introduction": {
                    "codeName": note.code_name or "",
                    "releaseDate": int(datetime.combine(note.release_date, time()).timestamp() * 1000) if note.release_date else 0,
                    "upgradePosition": note.upgrade_position or "",
                    "coreUpgrades": note.core_upgrades or []
                },
                "introduction_en": {
                    "codeName": note.code_name_en or note.code_name or "",
                    "releaseDate": int(datetime.combine(note.release_date, time()).timestamp() * 1000) if note.release_date else 0,
                    "upgradePosition": note.upgrade_position_en or note.upgrade_position or "",
                    "coreUpgrades": note.core_upgrades_en or []
                }
            }
            
            return note.version, version_info
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return None, None
    
    @staticmethod
    def get_version_introduction(db: Session, version: str) -> Optional[Dict[str, Any]]:
        """
        从数据库获取指定版本说明（优先读取已发布的版本）
        使用反射方式读取表结构，不依赖 premium 模型类
        
        Args:
            db: 数据库会话
            version: 版本号，如 "v0.2.7"
            
        Returns:
            版本说明字典，格式与 version_info.json 一致
            如果数据库中没有该版本，返回 None
        """
        try:
            metadata = MetaData()
            version_notes = Table('version_notes', metadata, autoload_with=db.engine)
            
            note = db.query(version_notes).filter(
                version_notes.c.version == version,
                version_notes.c.is_published == True
            ).first()
            
            if not note:
                return None
            
            return {
                "introduction": {
                    "codeName": note.code_name or "",
                    "releaseDate": int(datetime.combine(note.release_date, time()).timestamp() * 1000) if note.release_date else 0,
                    "upgradePosition": note.upgrade_position or "",
                    "coreUpgrades": note.core_upgrades or []
                },
                "introduction_en": {
                    "codeName": note.code_name_en or note.code_name or "",
                    "releaseDate": int(datetime.combine(note.release_date, time()).timestamp() * 1000) if note.release_date else 0,
                    "upgradePosition": note.upgrade_position_en or note.upgrade_position or "",
                    "coreUpgrades": note.core_upgrades_en or []
                }
            }
        except Exception:
            return None