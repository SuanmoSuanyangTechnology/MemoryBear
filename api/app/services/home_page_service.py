import json
from pathlib import Path
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session
from uuid import UUID
from typing import Dict, Any

from app.repositories.home_page_repository import HomePageRepository
from app.schemas.home_page_schema import HomeStatistics, WorkspaceInfo

class HomePageService:
    
    @staticmethod
    def get_home_statistics(db: Session, tenant_id: UUID) -> HomeStatistics:
        """获取首页统计数据"""
        # 计算时间范围
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 获取各项统计数据
        total_models, total_llm, total_embedding, model_week_growth_rate = HomePageRepository.get_model_statistics(
            db, tenant_id, week_start
        )
        
        active_workspaces, new_workspaces_this_week, workspace_week_growth_rate = HomePageRepository.get_workspace_statistics(
            db, tenant_id, week_start
        )
        
        total_users, new_users_this_week, user_week_growth_rate = HomePageRepository.get_user_statistics(
            db, tenant_id, week_start
        )
        
        running_apps, new_apps_this_week, app_week_growth_rate = HomePageRepository.get_app_statistics(
            db, tenant_id, week_start
        )
        
        return HomeStatistics(
            total_models=total_models,
            total_llm=total_llm,
            total_embedding=total_embedding,
            model_week_growth_rate=model_week_growth_rate,
            active_workspaces=active_workspaces,
            new_workspaces_this_week=new_workspaces_this_week,
            workspace_week_growth_rate=workspace_week_growth_rate,
            total_users=total_users,
            new_users_this_week=new_users_this_week,
            user_week_growth_rate=user_week_growth_rate,
            running_apps=running_apps,
            new_apps_this_week=new_apps_this_week,
            app_week_growth_rate=app_week_growth_rate
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

    @staticmethod
    def load_version_introduction(version: str) -> Dict[str, Any]:
        """
        从 JSON 文件加载对应版本的介绍
        :param version: 系统版本号（如 "0.2.0"）
        :return: 对应版本的详细介绍
        """
        # 1. 定义 JSON 文件路径（使用 Path 处理跨平台路径问题）
        json_file_path = Path(__file__).parent.parent / "version_info.json"
        # 转换为绝对路径，便于调试
        json_abs_path = json_file_path.resolve()

        try:
            # 2. 读取 JSON 文件
            if not json_abs_path.exists():
                return {
                    "message": f"版本介绍文件不存在：{json_abs_path}",
                    "codeName": "",
                    "releaseDate": "",
                    "upgradePosition": "",
                    "coreUpgrades": []
                }

            with open(json_abs_path, "r", encoding="utf-8") as f:
                changelogs = json.load(f)

            # 3. 匹配对应版本的介绍，若版本不存在返回默认提示
            if version not in changelogs:
                return {
                    "message": f"暂未查询到 {version} 版本的详细介绍",
                    "codeName": "",
                    "releaseDate": "",
                    "upgradePosition": "",
                    "coreUpgrades": []
                }
            return changelogs[version]

        except FileNotFoundError as e:
            # 处理文件不存在异常
            return {
                    "message": f"系统内部错误：{str(e)}",
                    "codeName": "",
                    "releaseDate": "",
                    "upgradePosition": "",
                    "coreUpgrades": []
                }
        except json.JSONDecodeError:
            # 处理 JSON 格式错误
            return {
                    "message": "版本介绍文件格式错误，无法解析 JSON",
                    "codeName": "",
                    "releaseDate": "",
                    "upgradePosition": "",
                    "coreUpgrades": []
                }
        except Exception as e:
            # 处理其他未知异常
            return {
                "message": f"加载版本介绍失败：{str(e)}",
                "codeName": "",
                "releaseDate": "",
                "upgradePosition": "",
                "coreUpgrades": []
            }