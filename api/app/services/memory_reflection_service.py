"""
记忆反思服务
处理反思引擎的调用和执行
"""
from datetime import datetime
from typing import Dict, Any, Optional, Set

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db import get_db
from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.reflection_engine import ReflectionConfig, ReflectionEngine
from app.core.memory.storage_services.reflection_engine.self_reflexion import ReflectionRange, ReflectionBaseline
from app.repositories.data_config_repository import DataConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.models.app_model import App
from app.models.app_release_model import AppRelease
from app.models.end_user_model import EndUser

api_logger = get_api_logger()


class WorkspaceAppService:
    """Workplace Application Service Class """
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_workspace_apps_detailed(self, workspace_id: str) -> Dict[str, Any]:
        """
            Get detailed information of all applications in the workspace

            Args:
                Workspace_id: Workspace ID

            Returns:
                Dictionary containing detailed application information
        """
        apps = self.db.query(App).filter(App.workspace_id == workspace_id).all()
        app_ids = [str(app.id) for app in apps]
        
        apps_detailed_info = []
        
        for app in apps:
            app_info = self._build_app_info(app)
            self._process_app_releases(app, app_info)
            self._process_end_users(app, app_info)
            apps_detailed_info.append(app_info)
        
        return {
            "status": "成功",
            "message": f"成功查询到 {len(app_ids)} 个应用及其详细信息",
            "workspace_id": str(workspace_id),
            "apps_count": len(app_ids),
            "app_ids": app_ids,
            "apps_detailed_info": apps_detailed_info
        }
    
    def _build_app_info(self, app: App) -> Dict[str, Any]:
        """base_infomation"""
        return {
            "id": str(app.id),
            "name": app.name,
            "description": app.description,
            "type": app.type,
            "status": app.status,
            "visibility": app.visibility,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None,
            "releases": [],
            "data_configs": [],
            "end_users": []
        }
    
    def _process_app_releases(self, app: App, app_info: Dict[str, Any]) -> None:
        """Process the release version and configuration information of the application"""
        app_releases = self.db.query(AppRelease).filter(AppRelease.app_id == app.id).all()
        
        if not app_releases:
            return

        processed_configs: Set[str] = set()
        
        for release in app_releases:
            memory_content = self._extract_memory_content(release.config)
            

            if memory_content and memory_content in processed_configs:
                continue
            
            release_info = {
                "app_id": str(release.app_id),
                "config": memory_content
            }
            

            if memory_content:
                processed_configs.add(memory_content)
                data_config_info = self._get_data_config(memory_content)
                
                if data_config_info:
                    if not any(dc["config_id"] == data_config_info["config_id"] for dc in app_info["data_configs"]):
                        app_info["data_configs"].append(data_config_info)
            
            app_info["releases"].append(release_info)
    
    def _extract_memory_content(self, config: Any) -> str:
        """Extract memory_comtent from config"""
        if not config or not isinstance(config, dict):
            return None
        
        memory_obj = config.get('memory')
        if memory_obj and isinstance(memory_obj, dict):
            return memory_obj.get('memory_content')
        
        return None
    
    def _get_data_config(self, memory_content: str) -> Dict[str, Any]:
        """Retrieve data_comfig information based on memory_comtent"""
        try:
            data_config_query, data_config_params = DataConfigRepository.build_select_reflection(memory_content)
            data_config_result = self.db.execute(text(data_config_query), data_config_params).fetchone()
            if data_config_result is None:
                return None
            
            if data_config_result:
                return {
                    "config_id": data_config_result.config_id,
                    "enable_self_reflexion": data_config_result.enable_self_reflexion,
                    "iteration_period": data_config_result.iteration_period,
                    "reflexion_range": data_config_result.reflexion_range,
                    "baseline": data_config_result.baseline,
                    "reflection_model_id": data_config_result.reflection_model_id,
                    "memory_verify": data_config_result.memory_verify,
                    "quality_assessment": data_config_result.quality_assessment,
                    "user_id": data_config_result.user_id
                }
        except Exception as e:
            api_logger.warning(f"查询data_config失败，memory_content: {memory_content}, 错误: {str(e)}")
        
        return None
    
    def _process_end_users(self, app: App, app_info: Dict[str, Any]) -> None:
        """Processing end-user information for applications"""
        end_users = self.db.query(EndUser).filter(EndUser.app_id == app.id).all()
        
        for end_user in end_users:
            end_user_info = {
                "id": str(end_user.id),
                "app_id": str(end_user.app_id)
            }
            app_info["end_users"].append(end_user_info)
    
    def get_end_user_reflection_time(self, end_user_id: str) -> Optional[Any]:
        """
        Read the reflection time of end users

        Args:
             End_user_id: End User ID

        Returns:
            Reflection time or None
        """
        try:
            end_user = self.db.query(EndUser).filter(EndUser.id == end_user_id).first()
            if end_user:
                return end_user.reflection_time
            return None
        except Exception as e:
            api_logger.error(f"读取用户反思时间失败，end_user_id: {end_user_id}, 错误: {str(e)}")
            return None
    
    def update_end_user_reflection_time(self, end_user_id: str) -> bool:
        """
        Update the reflection time of end users to the current time

        Args:
            End_user_id: End User ID

        Returns:
            Is the update successful
        """
        try:
            from datetime import datetime
            
            end_user = self.db.query(EndUser).filter(EndUser.id == end_user_id).first()
            if end_user:
                end_user.reflection_time = datetime.now()
                self.db.commit()
                api_logger.info(f"成功更新用户反思时间，end_user_id: {end_user_id}")
                return True
            else:
                api_logger.warning(f"未找到用户，end_user_id: {end_user_id}")
                return False
        except Exception as e:
            api_logger.error(f"更新用户反思时间失败，end_user_id: {end_user_id}, 错误: {str(e)}")
            self.db.rollback()
            return False


class MemoryReflectionService:
    """Memory reflection service category"""
    
    def __init__(self,db: Session = Depends(get_db)):
        self.db=db

    
    async def start_reflection_from_data(self, config_data: Dict[str, Any], end_user_id: str) -> Dict[str, Any]:
        """
        Starting Reflection from Configuration Data
        
        Args:
            config_data: Configure data dictionary, including reflective configuration information
            end_user_id: end_user_id
            
        Returns:
            Reflect on the execution results
        """
        try:
            config_id = config_data.get("config_id")
            api_logger.info(f"从配置数据启动反思，config_id: {config_id}, end_user_id: {end_user_id}")
            

            if not config_data.get("enable_self_reflexion", False):
                return {
                    "status": "跳过",
                    "message": "反思引擎未启用",
                    "config_id": config_id,
                    "end_user_id": end_user_id,
                    "config_data": config_data
                }
            

            config_data_id=config_data['config_id']
            reflection_config=WorkspaceAppService(self.db)._get_data_config(config_data_id)
            if reflection_config is not None and reflection_config['enable_self_reflexion']:
                reflection_config=  self._create_reflection_config_from_data(reflection_config)
                iteration_period=reflection_config.iteration_period
                workspace_service = WorkspaceAppService(self.db)
                current_reflection_time = workspace_service.get_end_user_reflection_time(end_user_id)

                reflection_time = datetime.fromisoformat(str(current_reflection_time))

                current_time = datetime.now()
                time_diff = current_time - reflection_time
                hours_diff = int(time_diff.total_seconds() / 3600)
                if iteration_period==hours_diff or current_reflection_time is None:
                    api_logger.info(f"与上次的反思时间间隔为: {hours_diff} 小时")
                    # 3. 执行反思引擎
                    reflection_results = await self._execute_reflection_engine(
                        reflection_config, end_user_id
                    )
                    # 更新反思时间为当前时间
                    update_success = workspace_service.update_end_user_reflection_time(end_user_id)
                    if update_success:
                        api_logger.info(f"成功更新用户 {end_user_id} 的反思时间")
                    else:
                        api_logger.error(f"更新用户 {end_user_id} 的反思时间失败")

                    return {
                        "status": "完成",
                        "message": "反思引擎执行完成",
                        "config_id": config_id,
                        "end_user_id": end_user_id,
                        "config_data": config_data,
                        "reflection_results": reflection_results
                    }
                else:
                    return {
                        "status": "等待中..",
                        "message": "反思引擎未开始执行执",
                        "config_id": config_id,
                        "end_user_id": end_user_id,
                        "config_data": config_data,
                        "reflection_results": ''
                    }
            
        except Exception as e:
            config_id = config_data.get("config_id", "unknown")
            api_logger.error(f"启动反思失败，config_id: {config_id}, end_user_id: {end_user_id}, 错误: {str(e)}")
            return {
                "status": "错误",
                "message": f"启动反思失败: {str(e)}",
                "config_id": config_id,
                "end_user_id": end_user_id,
                "config_data": config_data
            }
    
    def _create_reflection_config_from_data(self, config_data: Dict[str, Any]) -> ReflectionConfig:
        """Create reflective configuration objects from configuration data"""

        reflexion_range_value = config_data.get("reflexion_range")
        if reflexion_range_value is None or reflexion_range_value == "":
            reflexion_range_value = "partial"
        reflexion_range = ReflectionRange(reflexion_range_value)
        
        baseline_value = config_data.get("baseline")
        if baseline_value is None or baseline_value == "":
            baseline_value = "TIME"
        baseline = ReflectionBaseline(baseline_value)
        
        # iteration_period =
        iteration_period = config_data.get("iteration_period", 24)
        if isinstance(iteration_period, str):
            try:
                iteration_period = int(iteration_period)
            except (ValueError, TypeError):
                iteration_period = 24  # 默认24小时
        
        return ReflectionConfig(
            enabled=config_data.get("enable_self_reflexion", False),
            iteration_period=str(iteration_period),  # ReflectionConfig期望字符串
            reflexion_range=reflexion_range,
            baseline=baseline,
            memory_verify=config_data.get("memory_verify", False),
            quality_assessment=config_data.get("quality_assessment", False),
            model_id=config_data.get("reflection_model_id", "")
        )
    
    async def _execute_reflection_engine(
        self, 
        reflection_config: ReflectionConfig, 
        user_id: str
    ) -> Dict[str, Any]:
        """Execute Reflection Engine"""
        try:
            # 创建Neo4j连接器
            connector = Neo4jConnector()
            
            # 创建反思引擎
            engine = ReflectionEngine(
                config=reflection_config,
                neo4j_connector=connector,
                llm_client=reflection_config.model_id
            )
            
            # 执行反思
            reflection_result = await engine.execute_reflection(user_id)
            
            return {
                "success": reflection_result.success,
                "message": reflection_result.message,
                "conflicts_found": reflection_result.conflicts_found,
                "conflicts_resolved": reflection_result.conflicts_resolved,
                "memories_updated": reflection_result.memories_updated,
                "execution_time": reflection_result.execution_time,
                "details": reflection_result.details
            }
            
        except Exception as e:
            api_logger.error(f"反思引擎执行失败: {str(e)}")
            return {
                "success": False,
                "message": f"反思引擎执行失败: {str(e)}",
                "conflicts_found": 0,
                "conflicts_resolved": 0,
                "memories_updated": 0,
                "execution_time": 0.0
            }


class Memory_Reflection_Service:
    """Memory Reflection Service - Used for calling the/reflection interface"""
    
    def __init__(self, db: Session):
        self.db = db
        self.reflection_service = MemoryReflectionService(db)
    
    async def start_reflection(self, config_data: Dict[str, Any], end_user_id: str) -> Dict[str, Any]:
        """
        Activate the reflection function
        
        Args:
            config_data: 配置数据，格式如下：
                {
                    "config_id": 26,
                    "enable_self_reflexion": true,
                    "iteration_period": "6",
                    "reflexion_range": "partial",
                    "baseline": "TIME",
                    "reflection_model_id": "ea405fa6-c387-4d78-80ab-826d692301b3",
                    "memory_verify": true,
                    "quality_assessment": false,
                    "user_id": null
                }
            end_user_id: end_user_id，example "12a8b235-6eb1-4481-a53c-b77933b5c949"
            
        Returns:
        """
        api_logger.info(f"Memory_Reflection_Service启动反思，config_id: {config_data.get('config_id')}, end_user_id: {end_user_id}")
        
        # 调用核心反思服务
        result = await self.reflection_service.start_reflection_from_data(config_data, end_user_id)
        
        return result