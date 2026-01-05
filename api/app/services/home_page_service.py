from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from uuid import UUID

from app.repositories.home_page_repository import HomePageRepository
from app.schemas.home_page_schema import HomeStatistics, WorkspaceInfo

class HomePageService:
    
    @staticmethod
    def get_home_statistics(db: Session, tenant_id: UUID) -> HomeStatistics:
        """获取首页统计数据"""
        # 计算时间范围
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 获取各项统计数据
        total_models, new_models_this_month = HomePageRepository.get_model_statistics(
            db, tenant_id, month_start
        )
        
        active_workspaces, new_workspaces_this_month = HomePageRepository.get_workspace_statistics(
            db, tenant_id, month_start
        )
        
        total_users, new_users_this_month = HomePageRepository.get_user_statistics(
            db, tenant_id, month_start
        )
        
        running_apps, new_apps_this_week = HomePageRepository.get_app_statistics(
            db, tenant_id, week_start
        )
        
        return HomeStatistics(
            total_models=total_models,
            new_models_this_month=new_models_this_month,
            active_workspaces=active_workspaces,
            new_workspaces_this_month=new_workspaces_this_month,
            total_users=total_users,
            new_users_this_month=new_users_this_month,
            running_apps=running_apps,
            new_apps_this_week=new_apps_this_week
        )
    
    @staticmethod
    def get_workspace_list(db: Session, tenant_id: UUID) -> list[WorkspaceInfo]:
        """获取工作空间列表（优化版本）"""
        workspaces, app_count_dict, user_count_dict= HomePageRepository.get_workspaces_with_counts(
            db, tenant_id
        )
        
        workspace_list = []
        for workspace in workspaces:
            workspace_info = WorkspaceInfo(
                id=str(workspace.id),
                name=workspace.name,
                icon=workspace.icon,
                description=workspace.description,
                app_count=app_count_dict.get(workspace.id, 0),
                user_count=user_count_dict.get(workspace.id, 0),
                created_at=workspace.created_at
            )
            workspace_list.append(workspace_info)
        
        return workspace_list