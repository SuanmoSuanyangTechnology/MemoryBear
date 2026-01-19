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

    DEFAULT_RETURN_DATA: Dict[str, Any] = {
        "message": "",
        "introduction": {
            "codeName": "",
            "releaseDate": "",
            "upgradePosition": "",
            "coreUpgrades": []
        },
        "introduction_en": {
            "codeName": "",
            "releaseDate": "",
            "upgradePosition": "",
            "coreUpgrades": []
        }
    }
    
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
        # 2. 定义 JSON 文件路径（简化路径处理，保留绝对路径调试特性）
        json_abs_path = Path(__file__).parent.parent / "version_info.json"
        json_abs_path = json_abs_path.resolve()

        # 3. 初始化返回结果（深拷贝默认模板，避免修改原常量）
        from copy import deepcopy
        result = deepcopy(HomePageService.DEFAULT_RETURN_DATA)

        try:
            # 4. 简化文件存在性判断（合并逻辑，减少分支）
            if not json_abs_path.exists():
                result["message"] = f"版本介绍文件不存在：{json_abs_path}"
                return result

            # 5. 读取并解析 JSON 文件（简化文件操作流程）
            with open(json_abs_path, "r", encoding="utf-8") as f:
                changelogs = json.load(f)

            # 6. 简化版本匹配逻辑，直接返回结果或更新提示信息
            if version in changelogs:
                return changelogs[version]
            result["message"] = f"暂未查询到 {version} 版本的详细介绍"
            return result

        except FileNotFoundError as e:
            result["message"] = f"系统内部错误：{str(e)}"
            return result
        except json.JSONDecodeError:
            result["message"] = "版本介绍文件格式错误，无法解析 JSON"
            return result
        except Exception as e:
            result["message"] = f"加载版本介绍失败：{str(e)}"
            return result