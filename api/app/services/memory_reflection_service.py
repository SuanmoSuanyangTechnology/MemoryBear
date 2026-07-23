"""
记忆反思服务
处理反思引擎的调用和执行
"""
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, Set

from sqlalchemy.orm import Session
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.reflection_engine import ReflectionConfig, ReflectionEngine
from app.core.memory.storage_services.reflection_engine.self_reflexion import ReflectionRange, ReflectionBaseline
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.models.app_model import App
from app.models.app_release_model import AppRelease
from app.models.workspace_model import Workspace
from app.models.end_user_model import EndUser
from app.repositories.end_user_repository import EndUserRepository
from app.utils.config_utils import resolve_config_id, resolve_config_id_async

api_logger = get_api_logger()


async def get_workspace_apps_detailed_async(db: AsyncSession, workspace_id: str) -> Dict[str, Any]:
    """Get detailed information of all applications in the workspace (async version).

    Args:
        db: Async database session
        workspace_id: Workspace ID

    Returns:
        Dictionary containing detailed application information
    """# 需要repository
    result = await db.execute(
        select(App).where(App.workspace_id == uuid.UUID(workspace_id), App.is_active.is_(True))
    )
    apps = result.scalars().all()
    app_ids = [str(app.id) for app in apps]

    apps_detailed_info = []

    for app in apps:
        app_info = {
            "id": str(app.id),
            "name": app.name,
            "description": app.description,
            "type": app.type,
            "status": app.status,
            "visibility": app.visibility,
            "created_at": to_iso_z(app.created_at),
            "updated_at": to_iso_z(app.updated_at),
            "releases": [],
            "memory_configs": [],
            "end_users": []
        }

        # Process releases
        release_result = await db.execute(
            select(AppRelease).where(AppRelease.app_id == app.id)
        )
        app_releases = release_result.scalars().all()

        if app_releases:
            processed_configs: Set[str] = set()

            for release in app_releases:
                memory_content = _extract_memory_content(release.config, app.type)
                if memory_content and memory_content in processed_configs:
                    continue

                release_info = {
                    "app_id": str(release.app_id),
                    "config": memory_content
                }

                if memory_content:
                    processed_configs.add(memory_content)
                    memory_config_info = await _get_memory_config_async(db, memory_content)
                    if memory_config_info:
                        if not any(dc["config_id"] == memory_config_info["config_id"] for dc in app_info["memory_configs"]):
                            app_info["memory_configs"].append(memory_config_info)

                app_info["releases"].append(release_info)

        # Process end_users
        end_user_result = await db.execute(
            select(EndUser).where(EndUser.app_id == app.id, EndUser.is_active.is_(True))
        )
        end_users = end_user_result.scalars().all()

        for end_user in end_users:
            end_user_info = {
                "id": str(end_user.id),
                "app_id": str(end_user.app_id)
            }
            app_info["end_users"].append(end_user_info)

        apps_detailed_info.append(app_info)

    return {
        "status": "成功",
        "message": f"成功查询到 {len(app_ids)} 个应用及其详细信息",
        "workspace_id": str(workspace_id),
        "apps_count": len(app_ids),
        "app_ids": app_ids,
        "apps_detailed_info": apps_detailed_info
    }


def _extract_memory_content(release_config: Any, app_type: Optional[str] = None) -> Optional[str]:
    """Extract memory_config_id from release config (sync, no DB needed for extraction logic).

    The extraction itself is purely dict-level; legacy int resolution happens later
    via _get_memory_config_async with async DB support.

    Args:
        release_config: 发布配置字典（app_releases.config）
        app_type: 应用类型

    Returns:
        memory_config_id 字符串，不存在时返回 None
    """
    if not release_config or not isinstance(release_config, dict):
        return None

    if app_type:
        try:
            from app.services.memory_config_service import MemoryConfigService
            # Use None as db since we only need dict extraction (agent type)
            # Legacy int resolution will be handled by _get_memory_config_async
            config_id, _is_legacy = MemoryConfigService(None).extract_memory_config_id(app_type, release_config)
            if config_id:
                return str(config_id)
        except Exception as e:
            api_logger.warning(
                f"提取 memory_config_id 失败，app_type: {app_type}, 错误: {str(e)}"
            )

    # 回退：兼容旧 agent 结构（顶层 memory 对象）
    memory_obj = release_config.get('memory')
    if memory_obj and isinstance(memory_obj, dict):
        return memory_obj.get('memory_config_id') or memory_obj.get('memory_content')

    return None


async def _get_memory_config_async(db: AsyncSession, memory_content: str) -> Dict[str, Any]:
    """Retrieve memory_config information based on memory_content (async version).

    Args:
        db: Async database session
        memory_content: Memory config ID string

    Returns:
        Dict containing memory config info
    """
    try:
        resolved_id = await resolve_config_id_async(memory_content, db)
        memory_config_result = await MemoryConfigRepository(db).query_reflection_config_by_id_async(resolved_id)

        if memory_config_result:
            # 查询 workspace 获取 tenant_id
            tenant_id = None
            if memory_config_result.workspace_id:
                workspace = await db.get(Workspace, memory_config_result.workspace_id)
                tenant_id = str(workspace.tenant_id) if workspace and workspace.tenant_id else None

            return {
                "config_id": str(resolved_id),
                "workspace_id": memory_config_result.workspace_id,
                "tenant_id": tenant_id,
                "enable_self_reflexion": memory_config_result.enable_self_reflexion,
                "iteration_period": memory_config_result.iteration_period,
                "reflexion_range": memory_config_result.reflexion_range,
                "baseline": memory_config_result.baseline,
                "reflection_model_id": memory_config_result.reflection_model_id,
                "memory_verify": memory_config_result.memory_verify,
                "quality_assessment": memory_config_result.quality_assessment,
                "user_id": memory_config_result.user_id
            }
    except Exception as e:
        api_logger.warning(f"查询memory_config失败，memory_content: {memory_content}, 错误: {str(e)}")

    return None


class WorkspaceAppService:
    """Workplace Application Service Class """
    
    def __init__(self, db: Session):
        self.db = db

    def _extract_memory_content(self, release_config: Any, app_type: Optional[str] = None) -> Optional[str]:
        """Extract memory_config_id from release config（类型感知）

        - agent 应用：读取顶层 release_config.memory.memory_config_id
        - workflow / pure_workflow 应用：扫描 release_config.nodes 中的记忆节点

        复用 MemoryConfigService.extract_memory_config_id，按 app_type 分派；
        若 app_type 缺失或解析失败，回退到旧的 agent 顶层 memory 结构。

        Args:
            release_config: 发布配置字典（app_releases.config）
            app_type: 应用类型（agent / workflow / pure_workflow / multi_agent）

        Returns:
            memory_config_id 字符串，不存在时返回 None
        """
        if not release_config or not isinstance(release_config, dict):
            return None

        if app_type:
            from app.services.memory_config_service import MemoryConfigService
            try:
                config_id, _is_legacy = MemoryConfigService(self.db).extract_memory_config_id(app_type, release_config)
                if config_id:
                    return str(config_id)
            except Exception as e:
                api_logger.warning(
                    f"提取 memory_config_id 失败，app_type: {app_type}, 错误: {str(e)}"
                )

        # 回退：兼容旧 agent 结构（顶层 memory 对象）
        memory_obj = release_config.get('memory')
        if memory_obj and isinstance(memory_obj, dict):
            # 兼容新旧字段名：优先使用 memory_config_id，回退到 memory_content
            return memory_obj.get('memory_config_id') or memory_obj.get('memory_content')

        return None

    def _get_memory_config(self, memory_content: str) -> Dict[str, Any]:
        """Retrieve memory_config information based on memory_content
        
        Args:
            memory_content: Memory config ID string
            
        Returns:
            Dict containing memory config info including workspace_id and tenant_id for model fallback
        """
        try:
            memory_content = resolve_config_id(memory_content, self.db)
            memory_config_result = MemoryConfigRepository.query_reflection_config_by_id(self.db, memory_content)

            if memory_config_result:
                # 查询 workspace 获取 tenant_id，用于 SpeedBear 模型 API key 解析
                tenant_id = None
                if memory_config_result.workspace_id:
                    workspace = self.db.get(Workspace, memory_config_result.workspace_id)
                    tenant_id = str(workspace.tenant_id) if workspace and workspace.tenant_id else None

                return {
                    "config_id": memory_content,
                    "workspace_id": memory_config_result.workspace_id,
                    "tenant_id": tenant_id,
                    "enable_self_reflexion": memory_config_result.enable_self_reflexion,
                    "iteration_period": memory_config_result.iteration_period,
                    "reflexion_range": memory_config_result.reflexion_range,
                    "baseline": memory_config_result.baseline,
                    "reflection_model_id": memory_config_result.reflection_model_id,
                    "memory_verify": memory_config_result.memory_verify,
                    "quality_assessment": memory_config_result.quality_assessment,
                    "user_id": memory_config_result.user_id
                }
        except Exception as e:
            api_logger.warning(f"查询memory_config失败，memory_content: {memory_content}, 错误: {str(e)}")

        return None

    def get_end_user_reflection_time(self, end_user_id: str) -> Optional[Any]:
        """
        Read the reflection time of end users

        Args:
             End_user_id: End User ID

        Returns:
            Reflection time or None
        """
        try:
            end_user = EndUserRepository(self.db).get_end_user_by_id(uuid.UUID(end_user_id))
            if end_user:
                return end_user.reflection_time
            return None
        except Exception as e:
            api_logger.error(f"读取用户反思时间失败，end_user_id: {end_user_id}, 错误: {str(e)}")
            # 失败先 rollback，避免事务 aborted 拖垮同一 session 后续查询
            try:
                self.db.rollback()
            except Exception:
                pass
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

            end_user = EndUserRepository(self.db).get_end_user_by_id(uuid.UUID(end_user_id))
            if end_user:
                end_user.reflection_time = utcnow_naive()
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

    def get_end_user_write_time(self, end_user_id: str) -> Optional[Any]:
        """读取 end_user 的最后写入时间（write_time）。"""
        try:
            end_user = EndUserRepository(self.db).get_end_user_by_id(uuid.UUID(end_user_id))
            if end_user:
                return end_user.write_time
            return None
        except Exception as e:
            api_logger.error(f"读取用户写入时间失败，end_user_id: {end_user_id}, 错误: {str(e)}")
            # 失败先 rollback，避免事务 aborted 拖垮同一 session 后续查询
            try:
                self.db.rollback()
            except Exception:
                pass
            return None

    def update_end_user_write_time(self, end_user_id: str) -> bool:
        """将 end_user 的 write_time 更新为当前时间。"""
        try:
            end_user = EndUserRepository(self.db).get_end_user_by_id(uuid.UUID(end_user_id))
            if end_user:
                end_user.write_time = utcnow_naive()
                self.db.commit()
                api_logger.info(f"成功更新用户写入时间，end_user_id: {end_user_id}")
                return True
            else:
                api_logger.warning(f"未找到用户，end_user_id: {end_user_id}")
                return False
        except Exception as e:
            api_logger.error(f"更新用户写入时间失败，end_user_id: {end_user_id}, 错误: {str(e)}")
            self.db.rollback()
            return False


class MemoryReflectionService:
    """Memory reflection service category"""

    def __init__(self, db: Session):
        self.db = db

    async def start_text_reflection_async(
        self, config_data: Dict[str, Any], end_user_id: str, db: AsyncSession
    ) -> Dict[str, Any]:
        try:
            config_id = config_data.get("config_id")
            api_logger.info(f"从配置数据启动反思（异步），config_id: {config_id}, end_user_id: {end_user_id}")

            if not config_data.get("enable_self_reflexion", False):
                return {
                    "status": "跳过",
                    "message": "反思引擎未启用",
                    "config_id": config_id,
                    "end_user_id": end_user_id,
                    "config_data": config_data
                }

            config_data_id = config_data['config_id']
            reflection_config = await _get_memory_config_async(db, config_data_id)
            if reflection_config is not None and reflection_config['enable_self_reflexion']:
                reflection_config = await self._create_reflection_config_from_data(reflection_config)
                # 3. 执行反思引擎
                reflection_results = await self._execute_reflection_engine(
                    reflection_config, end_user_id
                )
                return {
                    "status": "完成",
                    "message": "反思引擎执行完成",
                    "config_id": config_id,
                    "end_user_id": end_user_id,
                    "config_data": config_data,
                    "reflection_results": reflection_results
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
            reflection_config=WorkspaceAppService(self.db)._get_memory_config(config_data_id)
            if reflection_config is not None and reflection_config['enable_self_reflexion']:
                reflection_config = await self._create_reflection_config_from_data(reflection_config)
                iteration_period = int(reflection_config.iteration_period)
                workspace_service = WorkspaceAppService(self.db)
                current_reflection_time = workspace_service.get_end_user_reflection_time(end_user_id)

                # 检查是否需要执行反思
                should_execute = False
                hours_diff = 0

                if current_reflection_time is None:
                    # 首次执行反思
                    should_execute = True
                    api_logger.info(f"首次执行反思，end_user_id: {end_user_id}")
                else:
                    # 计算时间差
                    try:
                        if isinstance(current_reflection_time, str):
                            reflection_time = datetime.fromisoformat(current_reflection_time)
                        else:
                            reflection_time = current_reflection_time

                        current_time = utcnow_naive()
                        time_diff = current_time - reflection_time
                        hours_diff = int(time_diff.total_seconds() / 3600)

                        # 检查是否达到反思周期
                        if hours_diff >= iteration_period:
                            should_execute = True
                            api_logger.info(f"与上次的反思时间间隔为: {hours_diff} 小时，达到周期 {iteration_period} 小时")
                        else:
                            api_logger.info(f"与上次的反思时间间隔为: {hours_diff} 小时，未达到周期 {iteration_period} 小时")
                    except (ValueError, TypeError) as e:
                        api_logger.warning(f"解析反思时间失败: {e}，将执行反思")
                        should_execute = True

                if should_execute:
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
                        "status": "等待中",
                        "message": f"反思引擎未开始执行，距离下次执行还需 {iteration_period - hours_diff} 小时",
                        "config_id": config_id,
                        "end_user_id": end_user_id,
                        "config_data": config_data,
                        "hours_since_last_reflection": hours_diff,
                        "next_reflection_in_hours": iteration_period - hours_diff
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

    async def _create_reflection_config_from_data(self, config_data: Dict[str, Any]) -> ReflectionConfig:
        """Create reflective configuration objects from configuration data

        If reflection_model_id is not set, falls back to workspace default LLM.

        Args:
            config_data: Dict containing reflection config including workspace_id

        Returns:
            ReflectionConfig object with model_id resolved
        """
        from app.repositories.workspace_repository import WorkspaceRepository

        reflexion_range_value = config_data.get("reflexion_range")
        if reflexion_range_value is None or reflexion_range_value == "":
            reflexion_range_value = "partial"

        # Map legacy/invalid values to valid enum values
        reflexion_range_mapping = {
            "retrieval": "partial",  # Map old 'retrieval' to 'partial'
            "partial": "partial",
            "all": "all"
        }
        reflexion_range_value = reflexion_range_mapping.get(reflexion_range_value, "partial")
        reflexion_range = ReflectionRange(reflexion_range_value)

        baseline_value = config_data.get("baseline")
        if baseline_value is None or baseline_value == "":
            baseline_value = "TIME"
        baseline = ReflectionBaseline(baseline_value)

        # iteration_period
        iteration_period = config_data.get("iteration_period", 24)
        if isinstance(iteration_period, str):
            try:
                iteration_period = int(iteration_period)
            except (ValueError, TypeError):
                iteration_period = 24  # 默认24小时

        # 获取 model_id 并转换为字符串（如果是 UUID 对象）
        reflection_model_id = config_data.get("reflection_model_id", "")
        if reflection_model_id:
            reflection_model_id = str(reflection_model_id)

        # 如果 reflection_model_id 为空，回退到工作空间默认 LLM
        if not reflection_model_id:
            workspace_id = config_data.get("workspace_id")
            if workspace_id:
                repo = WorkspaceRepository(self.db)
                if isinstance(self.db, AsyncSession):
                    workspace_models = await repo.get_workspace_models_configs_async(workspace_id)
                else:
                    workspace_models = repo.get_workspace_models_configs(workspace_id)
                if workspace_models and workspace_models.get("llm"):
                    reflection_model_id = workspace_models["llm"]
                    api_logger.info(
                        f"reflection_model_id 为空，使用工作空间默认 LLM: {reflection_model_id}"
                    )
        
        return ReflectionConfig(
            enabled=config_data.get("enable_self_reflexion", False),
            iteration_period=str(iteration_period),  # ReflectionConfig期望字符串
            reflexion_range=reflexion_range,
            baseline=baseline,
            memory_verify=config_data.get("memory_verify", False),
            quality_assessment=config_data.get("quality_assessment", False),
            model_id=reflection_model_id,
            tenant_id=config_data.get("tenant_id")
        )
    
    async def _execute_reflection_engine(
        self, 
        reflection_config: ReflectionConfig, 
        user_id: str
    ) -> Dict[str, Any]:
        """Execute Reflection Engine"""
        try:
            from app.core.memory.pipelines.base_pipeline import ModelClientMixin

            # 创建Neo4j连接器
            connector = Neo4jConnector()
            
            # 提前构建 LLM 客户端（不再让 ReflectionEngine 内部 lazy init）
            llm_client = await ModelClientMixin.get_llm_client_async(
                self.db, reflection_config.model_id, self._get_tenant_id(reflection_config)
            )
            
            # 创建反思引擎
            engine = ReflectionEngine(
                config=reflection_config,
                neo4j_connector=connector,
                llm_client=llm_client
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

    def _get_tenant_id(self, reflection_config: ReflectionConfig):
        """从 ReflectionConfig 中获取 tenant_id，用于 SpeedBear 模型 API key 解析"""
        tid = getattr(reflection_config, 'tenant_id', None)
        return uuid.UUID(tid) if tid else None


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
