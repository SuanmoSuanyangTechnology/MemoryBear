from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from uuid import UUID
from typing import Dict

from app.models.end_user_model import EndUser
from app.models.user_model import User
from app.models.workspace_model import Workspace, WorkspaceMember
from app.models.models_model import ModelConfig
from app.models.app_model import App

class HomePageRepository:
    
    @staticmethod
    def get_model_statistics(db: Session, tenant_id: UUID, month_start: datetime) -> tuple[int, int]:
        """获取模型统计数据"""
        total_models = db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active == True
        ).count()
        
        new_models_this_month = db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active == True,
            ModelConfig.created_at >= month_start
        ).count()
        
        return total_models, new_models_this_month
    
    @staticmethod
    def get_workspace_statistics(db: Session, tenant_id: UUID, month_start: datetime) -> tuple[int, int]:
        """获取工作空间统计数据"""
        active_workspaces = db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active == True
        ).count()
        
        new_workspaces_this_month = db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active == True,
            Workspace.created_at >= month_start
        ).count()
        
        return active_workspaces, new_workspaces_this_month
    
    @staticmethod
    def get_user_statistics(db: Session, tenant_id: UUID, month_start: datetime) -> tuple[int, int]:
        """获取用户统计数据"""
        workspace_ids = db.query(Workspace.id).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active == True
        ).subquery()

        total_users = db.query(EndUser).join(
            App,
            EndUser.app_id == App.id
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active == True,
            App.status == "active"
        ).count()

        new_users_this_month = db.query(EndUser).join(
            App,
            EndUser.app_id == App.id
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active == True,
            App.status == "active",
            EndUser.created_at >= month_start
        ).count()
        
        return total_users, new_users_this_month
    
    @staticmethod
    def get_app_statistics(db: Session, tenant_id: UUID, week_start: datetime) -> tuple[int, int]:
        """获取应用统计数据"""
        workspace_ids = db.query(Workspace.id).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active == True
        ).subquery()
        
        running_apps = db.query(App).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active == True,
            App.status == "active"
        ).count()
        
        new_apps_this_week = db.query(App).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active == True,
            App.status == "active",
            App.created_at >= week_start
        ).count()
        
        return running_apps, new_apps_this_week
    
    @staticmethod
    def get_workspaces_with_counts(db: Session, tenant_id: UUID) -> tuple[list[Workspace], Dict[UUID, int], Dict[UUID, int]]:
        """批量获取工作空间及其统计数据"""
        # 获取工作空间列表
        workspaces = db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active == True
        ).all()

        workspace_ids = [ws.id for ws in workspaces]
        
        # 批量获取应用数量
        app_counts = db.query(
            App.workspace_id,
            func.count(App.id).label('count')
        ).filter(
            App.workspace_id.in_(workspace_ids),
            App.is_active,
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
            App.is_active,
            App.status == "active"
        ).group_by(App.workspace_id).all()
        
        user_count_dict = {workspace_id: count for workspace_id, count in user_counts}
        
        return workspaces, app_count_dict, user_count_dict