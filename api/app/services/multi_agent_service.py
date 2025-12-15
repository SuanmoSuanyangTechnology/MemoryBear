"""多 Agent 配置管理服务"""
import uuid
from typing import Optional, List, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.models import MultiAgentConfig, App, AgentConfig
from app.schemas.multi_agent_schema import (
    MultiAgentConfigCreate,
    MultiAgentConfigUpdate,
    MultiAgentRunRequest
)
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.core.exceptions import ResourceNotFoundException, BusinessException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.models import AppRelease

logger = get_business_logger()


def convert_uuids_to_str(obj: Any) -> Any:
    """递归转换对象中的所有 UUID 为字符串
    
    Args:
        obj: 要转换的对象（dict, list, UUID 等）
        
    Returns:
        转换后的对象
    """
    if isinstance(obj, uuid.UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: convert_uuids_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuids_to_str(item) for item in obj]
    else:
        return obj


class MultiAgentService:
    """多 Agent 配置管理服务"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_config(
        self,
        app_id: uuid.UUID,
        data: MultiAgentConfigCreate,
        created_by: uuid.UUID
    ) -> MultiAgentConfig:
        """创建多 Agent 配置
        
        Args:
            app_id: 应用 ID
            data: 配置数据
            created_by: 创建者 ID
            
        Returns:
            多 Agent 配置
        """
        # 1. 验证应用存在
        app = self.db.get(App, app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))
        
        # 2. 检查是否已有有效配置
        existing = self.db.scalars(
            select(MultiAgentConfig)
            .where(
                MultiAgentConfig.app_id == app_id,
                MultiAgentConfig.is_active == True
            )
            .order_by(MultiAgentConfig.updated_at.desc())
        ).first()
        if existing:
            raise BusinessException("应用已有多 Agent 配置", BizCode.DUPLICATE_RESOURCE)
        
        # 3. 验证主 Agent 存在
        master_agent = self.db.get(AgentConfig, data.master_agent_id)
        if not master_agent:
            raise ResourceNotFoundException("主 Agent", str(data.master_agent_id))
        
        # 4. 验证子 Agent 存在
        for sub_agent in data.sub_agents:
            agent = self.db.get(AgentConfig, sub_agent.agent_id)
            if not agent:
                raise ResourceNotFoundException("子 Agent", str(sub_agent.agent_id))
        
        # 5. 创建配置（转换 UUID 为字符串以支持 JSON 序列化）
        sub_agents_data = [convert_uuids_to_str(sub_agent.model_dump()) for sub_agent in data.sub_agents]
        routing_rules_data = [convert_uuids_to_str(rule.model_dump()) for rule in data.routing_rules] if data.routing_rules else None
        
        # 处理 execution_config（可能是 None、字典或 Pydantic 模型）
        if data.execution_config is None:
            execution_config_data = {}
        elif isinstance(data.execution_config, dict):
            execution_config_data = convert_uuids_to_str(data.execution_config)
        else:
            execution_config_data = convert_uuids_to_str(data.execution_config.model_dump())
        
        config = MultiAgentConfig(
            app_id=app_id,
            master_agent_id=data.master_agent_id,
            master_agent_name=data.master_agent_name,
            orchestration_mode=data.orchestration_mode,
            sub_agents=sub_agents_data,
            routing_rules=routing_rules_data,
            execution_config=execution_config_data,
            aggregation_strategy=data.aggregation_strategy
        )
        
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        
        logger.info(
            "创建多 Agent 配置成功",
            extra={
                "config_id": str(config.id),
                "app_id": str(app_id),
                "mode": data.orchestration_mode,
                "sub_agent_count": len(data.sub_agents)
            }
        )
        
        return config
    
    def get_config(self, app_id: uuid.UUID) -> Optional[MultiAgentConfig]:
        """获取多 Agent 配置
        
        Args:
            app_id: 应用 ID
            
        Returns:
            多 Agent 配置，如果不存在返回 None
        """
        return self.db.scalars(
            select(MultiAgentConfig)
            .where(
                MultiAgentConfig.app_id == app_id,
                MultiAgentConfig.is_active == True
            )
            .order_by(MultiAgentConfig.updated_at.desc())
        ).first()
    
    def get_multi_agent_configs(self, app_id: uuid.UUID) -> Optional[dict]:
        """通过 app_id 获取最新有效的多智能体配置，并将 agent_id 转换为 app_id
        
        Args:
            app_id: 应用 ID
            
        Returns:
            转换后的配置字典，如果不存在返回 None
        """
        config = self.get_config(app_id)
        if not config:
            return None
        
        # 转换 master_agent_id (release_id) 为 app_id
        master_release = self.db.get(AppRelease, config.master_agent_id)
        master_app_id = master_release.app_id if master_release else config.master_agent_id
        
        # 转换 sub_agents 中的 agent_id (release_id) 为 app_id
        converted_sub_agents = []
        for sub_agent in config.sub_agents:
            sub_agent_copy = sub_agent.copy()
            release_id = sub_agent.get("agent_id")
            if release_id:
                try:
                    release_id_uuid = uuid.UUID(release_id) if isinstance(release_id, str) else release_id
                    sub_release = self.db.get(AppRelease, release_id_uuid)
                    if sub_release:
                        sub_agent_copy["agent_id"] = str(sub_release.app_id)
                except Exception as e:
                    logger.warning(f"转换 sub_agent agent_id 失败: {release_id}, 错误: {str(e)}")
            converted_sub_agents.append(sub_agent_copy)
        
        # 构建返回的配置字典
        return {
            "id": config.id,
            "app_id": config.app_id,
            "master_agent_id": master_app_id,
            "master_agent_name": config.master_agent_name,
            "orchestration_mode": config.orchestration_mode,
            "sub_agents": converted_sub_agents,
            "routing_rules": config.routing_rules,
            "execution_config": config.execution_config,
            "aggregation_strategy": config.aggregation_strategy,
            "is_active": config.is_active,
            "created_at": config.created_at,
            "updated_at": config.updated_at
        }
    
    def get_published_config_by_agent_id(self, agent_id: uuid.UUID) -> Optional[dict]:
        """通过 agent_id 获取当前发布版本的完整配置
        
        Args:
            agent_id: Agent 配置 ID
            
        Returns:
            当前发布版本的配置字典，如果没有发布版本则返回 None
        """
        from app.models import AppRelease
        
        # 查询 Agent 配置
        agent_config = self.db.get(AgentConfig, agent_id)
        if not agent_config:
            logger.warning(f"Agent 配置不存在: {agent_id}")
            return None
                
        # 获取关联的应用
        app = self.db.get(App, agent_config.app_id)
        if not app or not app.current_release_id:
            logger.warning(f"应用未发布或不存在: app_id={agent_config.app_id}")
            return None
        
        # 获取当前发布版本
        release = self.db.get(AppRelease, app.current_release_id)
        if not release:
            logger.warning(f"发布版本不存在: release_id={app.current_release_id}")
            return None
        
        # 从发布版本的 config 中获取完整配置
        # config 是一个 JSON 对象，包含了发布时的配置快照
        config_data = release.config
        if config_data and isinstance(config_data, dict):
            return config_data
        
        return None

    def get_published_by_agent_id(self, agent_id: uuid.UUID) -> Optional[AppRelease]:
        """通过 agent_id 获取当前发布版本的完整配置
        
        Args:
            agent_id: Agent 配置 ID
            
        Returns:
            当前发布版本的配置字典，如果没有发布版本则返回 None
        """
                
        # 获取关联的应用
        app = self.db.get(App, agent_id)
        if not app or not app.current_release_id:
            logger.warning(f"应用未发布或不存在: app_id={agent_id}")
            return None
        
        # 获取当前发布版本
        release = self.db.get(AppRelease, app.current_release_id)
        if not release:
            logger.warning(f"发布版本不存在: release_id={app.current_release_id}")
            return None
        return release
    
    def update_config(
        self,
        app_id: uuid.UUID,
        data: MultiAgentConfigUpdate
    ) -> MultiAgentConfig:
        """更新多 Agent 配置
        
        Args:
            app_id: 应用 ID
            data: 更新数据
            
        Returns:
            更新后的配置
        """
        config = self.get_config(app_id)
        if not config:
            # 1. 验证应用存在
            app = self.db.get(App, app_id)
            if not app:
                raise ResourceNotFoundException("应用", str(app_id))
            
            # 2. 验证主 Agent 存在并获取发布版本 ID
            master_app_release = self.get_published_by_agent_id(data.master_agent_id)
            if not master_app_release:
                raise ResourceNotFoundException("主 Agent 未发布或不存在", str(data.master_agent_id))
            
            # 使用发布版本 ID
            data.master_agent_id = master_app_release.id

            # 3. 验证子 Agent 存在并获取发布版本 ID
            for sub_agent in data.sub_agents:
                agent_app_release = self.get_published_by_agent_id(sub_agent.agent_id)
                if not agent_app_release:
                    raise ResourceNotFoundException("子 Agent 未发布或不存在", str(sub_agent.agent_id))
                
                # 使用发布版本 ID
                sub_agent.agent_id = agent_app_release.id
                
            
            # 5. 创建配置（转换 UUID 为字符串以支持 JSON 序列化）
            sub_agents_data = [convert_uuids_to_str(sub_agent.model_dump()) for sub_agent in data.sub_agents]
            # routing_rules_data = [convert_uuids_to_str(rule.model_dump()) for rule in data.routing_rules] if data.routing_rules else None
            
            # 处理 execution_config（可能是 None、字典或 Pydantic 模型）
            if data.execution_config is None:
                execution_config_data = {}
            elif isinstance(data.execution_config, dict):
                execution_config_data = convert_uuids_to_str(data.execution_config)
            else:
                execution_config_data = convert_uuids_to_str(data.execution_config.model_dump())
            
            config = MultiAgentConfig(
                app_id=app_id,
                master_agent_id=data.master_agent_id,
                master_agent_name=data.master_agent_name,
                orchestration_mode=data.orchestration_mode,
                sub_agents=sub_agents_data,
                # routing_rules=routing_rules_data,
                execution_config=execution_config_data,
                aggregation_strategy=data.aggregation_strategy
            )
            
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            
            logger.info(
                "创建多 Agent 配置成功",
                extra={
                    "config_id": str(config.id),
                    "app_id": str(app_id),
                    "mode": data.orchestration_mode,
                    "sub_agent_count": len(data.sub_agents)
                }
            )
            return config
            # raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        
        # 更新字段
        if data.master_agent_id is not None:
            # 验证主 Agent 存在
            # 3. 验证主 Agent 存在并获取发布配置
            master_app_release = self.get_published_by_agent_id(data.master_agent_id)
            if not master_app_release:
                raise ResourceNotFoundException("主 Agent 未发布或", str(data.master_agent_id))

            config.master_agent_id = master_app_release.id
        
        if data.master_agent_name is not None:
            config.master_agent_name = data.master_agent_name
        
        if data.orchestration_mode is not None:
            config.orchestration_mode = data.orchestration_mode
        
        if data.sub_agents is not None:
            # 验证子 Agent 存在，并获取其发布的 config_id
            updated_sub_agents = []
            for sub_agent in data.sub_agents:
                agent_app_release = self.get_published_by_agent_id(sub_agent.agent_id)
                if not agent_app_release:
                    raise ResourceNotFoundException("子 Agent 未发布或", str(sub_agent.agent_id))
                sub_agent.agent_id = agent_app_release.id               
                sub_agent_dict = convert_uuids_to_str(sub_agent.model_dump())
                updated_sub_agents.append(sub_agent_dict)
            
            config.sub_agents = updated_sub_agents
        
        # if data.routing_rules is not None:
        #     config.routing_rules = [convert_uuids_to_str(rule.model_dump()) for rule in data.routing_rules] if data.routing_rules else None
        
        if data.execution_config is not None:
            if isinstance(data.execution_config, dict):
                execution_config_data = convert_uuids_to_str(data.execution_config)
            else:
                execution_config_data = convert_uuids_to_str(data.execution_config.model_dump())
            config.execution_config = execution_config_data

        if data.aggregation_strategy is not None:
            config.aggregation_strategy = data.aggregation_strategy
        
        if data.is_active is not None:
            config.is_active = data.is_active
        
        self.db.commit()
        self.db.refresh(config)
        
        logger.info(
            "更新多 Agent 配置成功",
            extra={
                "config_id": str(config.id),
                "app_id": str(app_id)
            }
        )
        
        return config
    
    def delete_config(self, app_id: uuid.UUID) -> None:
        """删除多 Agent 配置
        
        Args:
            app_id: 应用 ID
        """
        config = self.get_config(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        
        # 逻辑删除多 Agent 配置
        config.is_active = False
        self.db.commit()
        
        logger.info(
            "删除多 Agent 配置成功",
            extra={
                "config_id": str(config.id),
                "app_id": str(app_id)
            }
        )
    
    async def run(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest
    ) -> dict:
        """运行多 Agent 任务
        
        Args:
            app_id: 应用 ID
            request: 运行请求
            
        Returns:
            执行结果
        """
        # 1. 获取配置
        config = self.get_config(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        
        if not config.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.RESOURCE_DISABLED)
        
        # 2. 创建编排器
        orchestrator = MultiAgentOrchestrator(self.db, config)
        
        # 3. 执行任务
        result = await orchestrator.execute(
            message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            variables=request.variables,
            use_llm_routing=getattr(request, 'use_llm_routing', True),  # 默认启用 LLM 路由
            web_search=getattr(request, 'web_search', False),  # 网络搜索参数
            memory=getattr(request, 'memory', True)  # 记忆功能参数
        )
        
        return result
    
    async def run_stream(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest,
        storage_type :str,
        user_rag_memory_id :str
    ):
        """运行多 Agent 任务（流式返回）
        
        Args:
            app_id: 应用 ID
            request: 运行请求
            
        Yields:
            SSE 格式的事件流
        """
        # 1. 获取配置
        config = self.get_config(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        
        if not config.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.RESOURCE_DISABLED)
        
        # 2. 创建编排器
        orchestrator = MultiAgentOrchestrator(self.db, config)
        
        # 3. 流式执行任务
        async for event in orchestrator.execute_stream(
            message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            variables=request.variables,
            use_llm_routing=getattr(request, 'use_llm_routing', True),
            web_search=getattr(request, 'web_search', False),  # 网络搜索参数
            memory=getattr(request, 'memory', True) , # 记忆功能参数
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id
        ):
            yield event
    
    def add_sub_agent(
        self,
        app_id: uuid.UUID,
        agent_id: uuid.UUID,
        name: str,
        role: Optional[str] = None,
        priority: int = 1,
        capabilities: Optional[List[str]] = None
    ) -> MultiAgentConfig:
        """添加子 Agent
        
        Args:
            app_id: 应用 ID
            agent_id: Agent ID
            name: Agent 名称
            role: 角色描述
            priority: 优先级
            capabilities: 能力列表
            
        Returns:
            更新后的配置
        """
        config = self.get_config(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        
        # 验证 Agent 存在
        agent = self.db.get(AgentConfig, agent_id)
        if not agent:
            raise ResourceNotFoundException("Agent", str(agent_id))
        
        # 检查是否已存在
        for sub_agent in config.sub_agents:
            if sub_agent["agent_id"] == str(agent_id):
                raise BusinessException("Agent 已存在于配置中", BizCode.DUPLICATE_RESOURCE)
        
        # 添加子 Agent
        new_sub_agent = {
            "agent_id": str(agent_id),
            "name": name,
            "role": role,
            "priority": priority,
            "capabilities": capabilities or []
        }
        
        config.sub_agents.append(new_sub_agent)
        
        # 标记为已修改
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        
        logger.info(
            "添加子 Agent 成功",
            extra={
                "config_id": str(config.id),
                "agent_id": str(agent_id),
                "agent_name": name
            }
        )
        
        return config
    
    def remove_sub_agent(
        self,
        app_id: uuid.UUID,
        agent_id: uuid.UUID
    ) -> MultiAgentConfig:
        """移除子 Agent
        
        Args:
            app_id: 应用 ID
            agent_id: Agent ID
            
        Returns:
            更新后的配置
        """
        config = self.get_config(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        
        # 查找并移除
        original_count = len(config.sub_agents)
        config.sub_agents = [
            sub_agent for sub_agent in config.sub_agents
            if sub_agent["agent_id"] != str(agent_id)
        ]
        
        if len(config.sub_agents) == original_count:
            raise ResourceNotFoundException("子 Agent", str(agent_id))
        
        # 标记为已修改
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        
        logger.info(
            "移除子 Agent 成功",
            extra={
                "config_id": str(config.id),
                "agent_id": str(agent_id)
            }
        )
        
        return config
    
    def list_configs(
        self,
        workspace_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20
    ) -> Tuple[List[MultiAgentConfig], int]:
        """列出多 Agent 配置
        
        Args:
            workspace_id: 工作空间 ID
            page: 页码
            pagesize: 每页数量
            
        Returns:
            配置列表和总数
        """
        # 构建查询
        stmt = (
            select(MultiAgentConfig)
            .join(App)
            .where(App.workspace_id == workspace_id)
            .order_by(desc(MultiAgentConfig.created_at))
        )
        
        # 总数
        count_stmt = stmt.with_only_columns(MultiAgentConfig.id)
        total = len(self.db.execute(count_stmt).all())
        
        # 分页
        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)
        configs = list(self.db.scalars(stmt).all())
        
        return configs, total
