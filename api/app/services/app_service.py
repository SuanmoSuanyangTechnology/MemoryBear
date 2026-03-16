"""
应用服务层

提供应用管理的业务逻辑，包括：
- 应用的创建、更新、查询
- Agent 配置管理
- 应用发布和版本管理
- 应用回滚
"""
import datetime
import uuid
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import Depends
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import (
    BusinessException,
    ResourceNotFoundException,
)
from app.core.logging_config import get_business_logger
from app.core.workflow.validator import WorkflowValidator
from app.db import get_db
from app.models import (
    AgentConfig,
    App,
    AppRelease,
    AppShare,
    MultiAgentConfig,
    WorkflowConfig,
    Workspace,
)
from app.models.app_model import AppStatus, AppType
from app.repositories.app_repository import get_apps_by_id, AppRepository
from app.repositories.workflow_repository import WorkflowConfigRepository
from app.schemas import app_schema
from app.schemas.workflow_schema import WorkflowConfigUpdate
from app.services.agent_config_converter import AgentConfigConverter
from app.services.model_service import ModelApiKeyService
from app.services.workflow_service import WorkflowService
from app.utils.app_config_utils import model_parameters_to_dict

# 获取业务日志器
logger = get_business_logger()


class AppService:
    """应用服务类

    负责应用相关的所有业务逻辑处理，遵循单一职责原则。
    """

    def __init__(self, db: Session):
        """初始化应用服务

        Args:
            db: 数据库会话
        """
        self.db = db
        self.app_repo = AppRepository(self.db)

    # ==================== 私有辅助方法 ====================

    def _validate_workspace_access(self, app: App, workspace_id: Optional[uuid.UUID]) -> None:
        """验证工作空间访问权限（严格模式，用于修改操作）

        Args:
            app: 应用对象
            workspace_id: 工作空间ID

        Raises:
            BusinessException: 当应用不在指定工作空间时
        """
        if workspace_id is not None and app.workspace_id != workspace_id:
            logger.warning(
                "工作空间访问被拒",
                extra={"app_id": str(app.id), "workspace_id": str(workspace_id)}
            )
            raise BusinessException("应用不在指定工作空间中", BizCode.WORKSPACE_NO_ACCESS)

    def _check_app_accessible(self, app: App, workspace_id: Optional[uuid.UUID]) -> bool:
        """检查应用是否可访问（包括共享应用）

        Args:
            app: 应用对象
            workspace_id: 工作空间ID

        Returns:
            bool: 是否可访问
        """
        from app.models import AppShare

        if workspace_id is None:
            return True

        # 1. 检查是否是本工作空间的应用
        if app.workspace_id == workspace_id:
            return True

        # 2. 检查是否是共享给本工作空间的应用
        stmt = select(AppShare).where(
            AppShare.source_app_id == app.id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        return share is not None

    def  _validate_app_accessible(self, app: App, workspace_id: Optional[uuid.UUID]) -> None:
        """验证应用是否可访问（包括共享应用，用于只读操作）

        Args:
            app: 应用对象
            workspace_id: 工作空间ID

        Raises:
            BusinessException: 当应用不可访问时
        """
        if not self._check_app_accessible(app, workspace_id):
            logger.warning(
                "应用访问被拒",
                extra={"app_id": str(app.id), "workspace_id": str(workspace_id)}
            )
            raise BusinessException("应用不可访问", BizCode.WORKSPACE_NO_ACCESS)

    def _get_share_permission(self, app: App, workspace_id: Optional[uuid.UUID]) -> Optional[str]:
        """获取共享应用的权限

        Returns:
            None: 不是共享应用（是本工作空间的应用）
            'readonly': 只读共享
            'editable': 可编辑共享
        """
        from app.models import AppShare

        if workspace_id is None or app.workspace_id == workspace_id:
            return None  # 本工作空间的应用，不是共享的

        stmt = select(AppShare).where(
            AppShare.source_app_id == app.id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()
        return share.permission if share else None

    def _validate_app_writable(self, app: App, workspace_id: Optional[uuid.UUID]) -> None:
        """Validate that the app config is writable (owner only).

        Shared apps (both readonly and editable) cannot modify config.
        - Own workspace app: allowed
        - Any shared app: denied

        Raises:
            BusinessException: when app is not writable
        """
        if workspace_id is None:
            return

        # Own workspace app, allow
        if app.workspace_id == workspace_id:
            return

        logger.warning(
            "应用写操作被拒",
            extra={"app_id": str(app.id), "workspace_id": str(workspace_id)}
        )
        raise BusinessException("共享应用不可修改配置", BizCode.WORKSPACE_NO_ACCESS)

    def _get_app_or_404(self, app_id: uuid.UUID) -> App:
        """获取应用或抛出404异常

        Args:
            app_id: 应用ID

        Returns:
            App: 应用对象

        Raises:
            ResourceNotFoundException: 当应用不存在时
        """
        app = get_apps_by_id(self.db, app_id)
        if not app:
            logger.warning("应用不存在", extra={"app_id": str(app_id)})
            raise ResourceNotFoundException("应用", str(app_id))
        return app

    def _check_workflow_config(self, app_id: uuid.UUID):
        from sqlalchemy import select

        from app.core.exceptions import BusinessException
        from app.models import ModelConfig, WorkflowConfig
        # 2. 获取 Agent 配置
        stmt = select(WorkflowConfig).where(AgentConfig.app_id == app_id)
        agent_cfg = self.db.scalars(stmt).first()
        if not agent_cfg:
            raise BusinessException("Agent 配置不存在，无法试运行", BizCode.AGENT_CONFIG_MISSING)

        # 3. 获取模型配置
        model_config = None
        if agent_cfg.default_model_config_id:
            model_config = self.db.get(ModelConfig, agent_cfg.default_model_config_id)

        if not model_config:
            raise BusinessException("模型配置不存在，无法试运行", BizCode.AGENT_CONFIG_MISSING)

    def _check_agent_config(self, app_id: uuid.UUID):
        from sqlalchemy import select

        from app.core.exceptions import BusinessException
        from app.models import AgentConfig, ModelConfig
        # 2. 获取 Agent 配置
        stmt = select(AgentConfig).where(AgentConfig.app_id == app_id)
        agent_cfg = self.db.scalars(stmt).first()
        if not agent_cfg:
            raise BusinessException("Agent 配置不存在，无法试运行", BizCode.AGENT_CONFIG_MISSING)

        # 3. 获取模型配置
        model_config = None
        if agent_cfg.default_model_config_id:
            model_config = self.db.get(ModelConfig, agent_cfg.default_model_config_id)

        if not model_config:
            raise BusinessException("模型配置不存在，无法试运行", BizCode.AGENT_CONFIG_MISSING)

    def _check_multi_agent_config(self, app_id: uuid.UUID):
        """检查多智能体配置的完整性

        验证内容：
        1. 多智能体配置是否存在
        2. 主 Agent 配置是否存在
        3. 子 Agent 配置是否存在
        4. 所有 Agent 的模型配置是否存在

        Args:
            app_id: 应用 ID

        Raises:
            BusinessException: 配置不完整或不存在时抛出
        """
        from app.models import ModelConfig
        from app.services.multi_agent_service import MultiAgentService

        # 1. 检查多智能体配置是否存在
        service = MultiAgentService(self.db)
        multi_agent_config = service.get_config(app_id)

        if not multi_agent_config:
            raise BusinessException(
                "多智能体配置不存在，无法运行",
                BizCode.AGENT_CONFIG_MISSING
            )

        if not multi_agent_config.is_active:
            raise BusinessException(
                "多智能体配置未激活，无法运行",
                BizCode.AGENT_CONFIG_MISSING
            )
        if multi_agent_config.orchestration_mode == "supervisor":
            if not multi_agent_config.default_model_config_id:
                # # 2. 检查主 Agent 配置
                if not multi_agent_config.master_agent_id:
                    raise BusinessException(
                        "未配置主 Agent，无法运行",
                        BizCode.AGENT_CONFIG_MISSING
                    )

                master_agent_release = self.db.get(AppRelease, multi_agent_config.master_agent_id)
                if not master_agent_release:
                    raise BusinessException(
                        f"主 Agent 配置不存在: {multi_agent_config.master_agent_id}",
                        BizCode.AGENT_CONFIG_MISSING
                    )

                # 检查主 Agent 的模型配置
                multi_agent_config.default_model_config_id = master_agent_release.default_model_config_id

            model_api_key = ModelApiKeyService.get_available_api_key(self.db, multi_agent_config.default_model_config_id)
            if not model_api_key:
                raise ResourceNotFoundException("模型配置", str(multi_agent_config.default_model_config_id))

        # 3. 检查子 Agent 配置
        if not multi_agent_config.sub_agents or len(multi_agent_config.sub_agents) == 0:
            raise BusinessException(
                "未配置子 Agent，无法运行",
                BizCode.AGENT_CONFIG_MISSING
            )

        # 4. 验证每个子 Agent 及其模型配置
        for idx, sub_agent_data in enumerate(multi_agent_config.sub_agents):
            agent_id = sub_agent_data.get('agent_id')
            if not agent_id:
                raise BusinessException(
                    f"子 Agent #{idx + 1} 缺少 agent_id",
                    BizCode.AGENT_CONFIG_MISSING
                )

            # 转换为 UUID
            try:
                from uuid import UUID
                agent_uuid = UUID(agent_id) if isinstance(agent_id, str) else agent_id
            except (ValueError, TypeError):
                raise BusinessException(
                    f"子 Agent #{idx + 1} 的 agent_id 格式无效: {agent_id}",
                    BizCode.INVALID_PARAMETER
                )

            # 检查子 Agent 是否存在
            sub_agent_release = self.db.get(AppRelease, agent_uuid)
            if not sub_agent_release:
                raise BusinessException(
                    f"子 Agent 配置不存在: {agent_id} ({sub_agent_data.get('name', '未命名')})",
                    BizCode.AGENT_CONFIG_MISSING
                )

            # 检查子 Agent 的模型配置
            if sub_agent_release.default_model_config_id:
                sub_model = self.db.get(ModelConfig, sub_agent_release.default_model_config_id)
                if not sub_model:
                    raise BusinessException(
                        f"子 Agent '{sub_agent_data.get('name', '未命名')}' 的模型配置不存在: {sub_agent_release.default_model_config_id}",
                        BizCode.MODEL_NOT_FOUND
                    )
            else:
                raise BusinessException(
                    f"子 Agent '{sub_agent_data.get('name', '未命名')}' 未配置模型，无法运行",
                    BizCode.MODEL_NOT_FOUND
                )

        logger.info(
            "多智能体配置检查通过"
        )

    def _create_agent_config(
            self,
            app_id: uuid.UUID,
            config_data: app_schema.AgentConfigCreate,
            now: datetime.datetime
    ) -> None:
        """创建 Agent 配置（内部方法）

        Args:
            app_id: 应用ID
            config_data: Agent 配置数据
            now: 当前时间
        """
        storage_data = AgentConfigConverter.to_storage_format(config_data)

        agent_cfg = AgentConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            system_prompt=config_data.system_prompt,
            default_model_config_id=config_data.default_model_config_id,
            model_parameters=storage_data.get("model_parameters"),
            knowledge_retrieval=storage_data.get("knowledge_retrieval"),
            memory=storage_data.get("memory"),
            variables=storage_data.get("variables", []),
            tools=storage_data.get("tools", []),
            skills=storage_data.get("skills", {}),
            features=storage_data.get("features", {}),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(agent_cfg)
        logger.debug("Agent 配置已创建", extra={"app_id": str(app_id)})

    def _create_workflow_config(
            self,
            app_id: uuid.UUID,
            data: app_schema.WorkflowConfigCreate,
            now: datetime.datetime
    ):
        workflow_cfg = WorkflowConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            nodes=[node.model_dump() for node in data.nodes] if data.nodes else [],
            edges=[edge.model_dump() for edge in data.edges] if data.edges else [],
            variables=[var.model_dump() for var in data.variables] if data.variables else [],
            execution_config=data.execution_config.model_dump() if data.execution_config else {},
            triggers=[trigger.model_dump() for trigger in data.triggers] if data.triggers else [],
            is_active=True,
            created_at=now,
            updated_at=now
        )
        self.db.add(workflow_cfg)

    def _create_multi_agent_config(
            self,
            app_id: uuid.UUID,
            config_data: Dict[str, Any],
            now: datetime.datetime
    ) -> None:
        """创建多 Agent 配置（内部方法）

        Args:
            app_id: 应用ID
            config_data: 多 Agent 配置数据（Dict）
            now: 当前时间
        """
        # 将 Dict 转换为 MultiAgentConfigCreate
        from app.schemas.multi_agent_schema import (
            ExecutionConfig,
            MultiAgentConfigCreate,
            RoutingRule,
            SubAgentConfig,
        )

        # 转换 sub_agents
        sub_agents = [SubAgentConfig(**sa) for sa in config_data.get('sub_agents', [])]

        # 转换 routing_rules（如果有）
        routing_rules = None
        if config_data.get('routing_rules'):
            routing_rules = [RoutingRule(**rr) for rr in config_data['routing_rules']]

        # 转换 execution_config
        execution_config = ExecutionConfig(**config_data.get('execution_config', {}))

        # 创建 MultiAgentConfigCreate 对象
        config = MultiAgentConfigCreate(
            master_agent_id=config_data['master_agent_id'],
            orchestration_mode=config_data['orchestration_mode'],
            sub_agents=sub_agents,
            routing_rules=routing_rules,
            execution_config=execution_config,
            aggregation_strategy=config_data.get('aggregation_strategy', 'merge')
        )

        # 验证主 Agent 存在
        master_agent = self.db.get(AgentConfig, config.master_agent_id)
        if not master_agent:
            raise ResourceNotFoundException("主 Agent", str(config.master_agent_id))

        # 验证子 Agent 存在
        for sub_agent in config.sub_agents:
            agent = self.db.get(AgentConfig, sub_agent.agent_id)
            if not agent:
                raise ResourceNotFoundException("子 Agent", str(sub_agent.agent_id))

        # 创建多 Agent 配置
        # 将 UUID 转换为字符串以便 JSON 序列化
        sub_agents_data = []
        for sub_agent in config.sub_agents:
            sa_dict = sub_agent.model_dump()
            sa_dict['agent_id'] = str(sa_dict['agent_id'])  # UUID -> str
            sub_agents_data.append(sa_dict)

        routing_rules_data = None
        if config.routing_rules:
            routing_rules_data = []
            for rule in config.routing_rules:
                rule_dict = rule.model_dump()
                rule_dict['target_agent_id'] = str(rule_dict['target_agent_id'])  # UUID -> str
                routing_rules_data.append(rule_dict)

        multi_agent_cfg = MultiAgentConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            master_agent_id=config.master_agent_id,
            orchestration_mode=config.orchestration_mode,
            sub_agents=sub_agents_data,
            routing_rules=routing_rules_data,
            execution_config=config.execution_config.model_dump(),
            aggregation_strategy=config.aggregation_strategy,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(multi_agent_cfg)
        logger.debug("多 Agent 配置已创建", extra={"app_id": str(app_id), "mode": config.orchestration_mode})

    def _get_next_version(self, app_id: uuid.UUID) -> int:
        """获取下一个版本号

        Args:
            app_id: 应用ID

        Returns:
            int: 下一个版本号
        """
        stmt = select(func.max(AppRelease.version)).where(AppRelease.app_id == app_id)
        max_ver = self.db.execute(stmt).scalar()
        return 1 if max_ver is None else int(max_ver) + 1

    def _convert_to_schema(
            self,
            app: App,
            current_workspace_id: uuid.UUID
    ) -> app_schema.App:
        """将 App 模型转换为 Schema，并设置 is_shared 字段

        Args:
            app: App 模型实例
            current_workspace_id: 当前工作空间ID

        Returns:
            app_schema.App: 应用 Schema
        """
        is_shared = app.workspace_id != current_workspace_id
        share_permission = None
        source_workspace_name = None
        source_workspace_icon = None
        source_app_version = None
        source_app_is_active = None

        if is_shared:
            # 查询共享权限和来源工作空间名称
            from app.models import AppShare
            stmt = select(AppShare).where(
                AppShare.source_app_id == app.id,
                AppShare.target_workspace_id == current_workspace_id,
                AppShare.is_active.is_(True)
            )
            share = self.db.scalars(stmt).first()
            if share:
                share_permission = share.permission
                if share.source_workspace:
                    source_workspace_name = share.source_workspace.name
                    source_workspace_icon = share.source_workspace.icon

        # 版本号和生效状态
        if app.current_release:
            source_app_version = app.current_release.version_name
        source_app_is_active = app.is_active

        app_dict = {
            "id": app.id,
            "workspace_id": app.workspace_id,
            "created_by": app.created_by,
            "name": app.name,
            "description": app.description,
            "icon": app.icon,
            "icon_type": app.icon_type,
            "type": app.type,
            "visibility": app.visibility,
            "status": app.status,
            "tags": app.tags or [],
            "current_release_id": app.current_release_id,
            "is_active": app.is_active,
            "is_shared": is_shared,
            "share_permission": share_permission,
            "source_workspace_name": source_workspace_name,
            "source_workspace_icon": source_workspace_icon,
            "source_app_version": source_app_version,
            "source_app_is_active": source_app_is_active,
            "created_at": app.created_at,
            "updated_at": app.updated_at
        }
        return app_schema.App(**app_dict)

    # ==================== 应用管理 ====================

    def get_app(
            self,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> App:
        """获取应用详情

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID（用于权限验证，支持共享应用）

        Returns:
            App: 应用对象

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不可访问时
        """
        app = self._get_app_or_404(app_id)
        self._validate_app_accessible(app, workspace_id)
        return app

    def create_app(
            self,
            *,
            user_id: uuid.UUID,
            workspace_id: uuid.UUID,
            data: app_schema.AppCreate
    ) -> App:
        """创建应用

        Args:
            user_id: 创建者用户ID
            workspace_id: 工作空间ID
            data: 应用创建数据

        Returns:
            App: 创建的应用对象

        Raises:
            BusinessException: 当创建失败时
        """
        logger.info(
            "创建应用",
            extra={"app_name": data.name, "type": data.type, "workspace_id": str(workspace_id)}
        )
        apps = self.app_repo.get_apps_by_name(data.name, data.type, workspace_id)
        if apps:
            raise BusinessException(message="已存在同名应用", code=BizCode.RESOURCE_ALREADY_EXISTS)

        try:
            now = datetime.datetime.now()

            app = App(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                created_by=user_id,
                name=data.name,
                description=data.description,
                icon=data.icon,
                icon_type=data.icon_type,
                type=data.type,
                visibility=data.visibility,
                status=data.status,
                tags=data.tags or [],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(app)
            self.db.flush()  # 获取 app.id

            # 如果是 agent 类型且提供了配置，创建 AgentConfig
            if app.type == "agent" and data.agent_config:
                self._create_agent_config(app.id, data.agent_config, now)

            # 如果是 multi_agent 类型且提供了配置，创建 MultiAgentConfig
            if app.type == "multi_agent" and data.multi_agent_config:
                self._create_multi_agent_config(app.id, data.multi_agent_config, now)

            if app.type == "workflow" and data.workflow_config:
                self._create_workflow_config(app.id, data.workflow_config, now)

            self.db.commit()
            self.db.refresh(app)

            logger.info("应用创建成功", extra={"app_id": str(app.id), "app_name": app.name})
            return app

        except Exception as e:
            self.db.rollback()
            logger.error("应用创建失败", extra={"app_name": data.name, "error": str(e)})
            raise BusinessException(f"应用创建失败: {str(e)}", BizCode.INTERNAL_ERROR, cause=e)

    def update_app(
            self,
            *,
            app_id: uuid.UUID,
            data: app_schema.AppUpdate,
            workspace_id: Optional[uuid.UUID] = None
    ) -> App:
        """更新应用基本信息

        Args:
            app_id: 应用ID
            data: 更新数据
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            App: 更新后的应用对象

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不在指定工作空间时
        """
        logger.info("更新应用", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        self._validate_app_writable(app, workspace_id)

        changed = False
        for field in ["name", "description", "icon", "icon_type", "visibility", "status", "tags"]:
            val = getattr(data, field, None)
            if val is not None:
                setattr(app, field, val)
                changed = True

        if changed:
            app.updated_at = datetime.datetime.now()
            self.db.commit()
            self.db.refresh(app)
            logger.info("应用更新成功", extra={"app_id": str(app_id)})
        else:
            logger.debug("应用无变更", extra={"app_id": str(app_id)})

        return app

    def delete_app(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> None:
        """删除应用

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID（用于权限验证）

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不在指定工作空间时
        """
        logger.info("删除应用", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        self._validate_workspace_access(app, workspace_id)

        # 逻辑删除应用
        app.is_active = False
        self.db.commit()

        logger.info(
            "应用删除成功",
            extra={
                "app_id": str(app_id),
                "app_name": app.name,
                "app_type": app.type
            }
        )

    def copy_app(
            self,
            *,
            app_id: uuid.UUID,
            user_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None,
            new_name: Optional[str] = None
    ) -> App:
        """复制应用（包括基础信息和配置）

        Args:
            app_id: 源应用ID
            user_id: 创建者用户ID
            workspace_id: 目标工作空间ID（如果为None，则复制到源应用所在工作空间）
            new_name: 新应用名称（如果为None，则使用"源应用名称 - 副本"）

        Returns:
            App: 复制后的新应用对象

        Raises:
            ResourceNotFoundException: 当源应用不存在时
            BusinessException: 当复制失败时
        """
        logger.info("复制应用", extra={"source_app_id": str(app_id)})

        try:
            # 获取源应用
            source_app = self._get_app_or_404(app_id)
            self._validate_app_accessible(source_app, workspace_id)

            # 确定目标工作空间
            target_workspace_id = workspace_id or source_app.workspace_id

            # 确定新应用名称
            if not new_name:
                new_name = f"{source_app.name} - 副本"

            now = datetime.datetime.now()

            # 创建新应用（复制基础信息）
            new_app = App(
                id=uuid.uuid4(),
                workspace_id=target_workspace_id,
                created_by=user_id,
                name=new_name,
                description=source_app.description,
                icon=source_app.icon,
                icon_type=source_app.icon_type,
                type=source_app.type,
                visibility=source_app.visibility,
                status="draft",  # 复制的应用默认为草稿状态
                tags=source_app.tags.copy() if source_app.tags else [],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(new_app)
            self.db.flush()

            # 如果是 agent 类型，复制 AgentConfig
            if source_app.type == AppType.AGENT:
                source_config = self.db.query(AgentConfig).filter(
                    AgentConfig.app_id == source_app.id
                ).first()

                if source_config:
                    new_config = AgentConfig(
                        id=uuid.uuid4(),
                        app_id=new_app.id,
                        system_prompt=source_config.system_prompt,
                        default_model_config_id=source_config.default_model_config_id,
                        model_parameters=source_config.model_parameters.copy() if source_config.model_parameters else None,
                        knowledge_retrieval=source_config.knowledge_retrieval.copy() if source_config.knowledge_retrieval else None,
                        memory=source_config.memory.copy() if source_config.memory else None,
                        variables=source_config.variables.copy() if source_config.variables else [],
                        tools=source_config.tools.copy() if source_config.tools else [],
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_config)

            elif source_app.type == AppType.WORKFLOW:
                source_config = self.db.query(WorkflowConfig).filter(
                    WorkflowConfig.app_id == source_app.id
                ).first()

                if source_config:
                    new_config = WorkflowConfig(
                        id=uuid.uuid4(),
                        app_id=new_app.id,
                        nodes=source_config.nodes.copy() if source_config.nodes else [],
                        edges=source_config.edges.copy() if source_config.edges else [],
                        variables=source_config.variables.copy() if source_config.variables else [],
                        execution_config=source_config.execution_config.copy() if source_config.execution_config else {},
                        triggers=source_config.triggers.copy() if source_config.triggers else [],
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_config)

            elif source_app.type == AppType.MULTI_AGENT:
                source_config = self.db.query(MultiAgentConfig).filter(
                    MultiAgentConfig.app_id == source_app.id
                ).first()

                if source_config:
                    new_config = MultiAgentConfig(
                        id=uuid.uuid4(),
                        app_id=new_app.id,
                        master_agent_id=source_config.master_agent_id,
                        master_agent_name=source_config.master_agent_name,
                        default_model_config_id=source_config.default_model_config_id,
                        model_parameters=source_config.model_parameters,
                        orchestration_mode=source_config.orchestration_mode,
                        sub_agents=source_config.sub_agents.copy() if source_config.sub_agents else [],
                        routing_rules=source_config.routing_rules.copy() if source_config.routing_rules else None,
                        execution_config=source_config.execution_config.copy() if source_config.execution_config else {},
                        aggregation_strategy=source_config.aggregation_strategy,
                        is_active=True,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_config)

            self.db.commit()
            self.db.refresh(new_app)

            logger.info(
                "应用复制成功",
                extra={
                    "source_app_id": str(app_id),
                    "new_app_id": str(new_app.id),
                    "new_app_name": new_app.name
                }
            )

            return new_app

        except Exception as e:
            self.db.rollback()
            logger.error(
                "应用复制失败",
                extra={"source_app_id": str(app_id), "error": str(e)}
            )
            raise BusinessException(f"应用复制失败: {str(e)}", BizCode.INTERNAL_ERROR, cause=e)

    def list_apps(
            self,
            *,
            workspace_id: uuid.UUID,
            type: Optional[str] = None,
            visibility: Optional[str] = None,
            status: Optional[str] = None,
            search: Optional[str] = None,
            include_shared: bool = True,
            shared_only: bool = False,
            page: int = 1,
            pagesize: int = 10,
    ) -> Tuple[List[App], int]:
        """列出工作空间中的应用（分页）

        包括：
        1. 本工作空间创建的应用
        2. 其他工作空间分享给本工作空间的应用（如果 include_shared=True）

        Args:
            workspace_id: 工作空间ID
            type: 应用类型过滤
            visibility: 可见性过滤
            status: 状态过滤
            search: 搜索关键词
            include_shared: 是否包含分享的应用
            page: 页码（从1开始）
            pagesize: 每页数量

        Returns:
            Tuple[List[App], int]: (应用列表, 总数)
        """
        from app.models import AppShare

        logger.debug(
            "查询应用列表",
            extra={
                "workspace_id": str(workspace_id),
                "include_shared": include_shared,
                "page": page,
                "pagesize": pagesize
            }
        )

        # 构建查询条件
        filters = [App.is_active.is_(True)]
        if type:
            filters.append(App.type == type)
        if visibility:
            filters.append(App.visibility == visibility)
        if status:
            filters.append(App.status == status)
        if search:
            filters.append(func.lower(App.name).like(f"%{search.lower()}%"))

        # shared_only implies include_shared; enforce to avoid confusing API usage
        if shared_only:
            include_shared = True

        # 基础查询：本工作空间的应用
        if shared_only:
            # 只返回共享给本工作空间的应用，不含自有应用
            shared_app_ids_stmt = (
                select(AppShare.source_app_id)
                .where(AppShare.target_workspace_id == workspace_id, AppShare.is_active.is_(True))
            )
            stmt = select(App).where(App.id.in_(shared_app_ids_stmt))
        elif include_shared:
            # 查询本工作空间的应用 + 分享给本工作空间的应用
            shared_app_ids_stmt = (
                select(AppShare.source_app_id)
                .where(AppShare.target_workspace_id == workspace_id, AppShare.is_active.is_(True))
            )
            stmt = select(App).where(
                or_(
                    App.workspace_id == workspace_id,
                    App.id.in_(shared_app_ids_stmt)
                )
            )
        else:
            # 只查询本工作空间的应用
            stmt = select(App).where(App.workspace_id == workspace_id)

        # 应用过滤条件
        if filters:
            stmt = stmt.where(and_(*filters))

        # 计算总数
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.db.execute(total_stmt).scalar() or 0

        # 分页
        offset = (page - 1) * pagesize
        stmt = stmt.order_by(App.created_at.desc()).offset(offset).limit(pagesize)

        items = list(self.db.scalars(stmt).all())

        logger.debug(
            "应用列表查询完成",
            extra={"total": total, "returned": len(items), "include_shared": include_shared}
        )
        return items, int(total)

    def get_apps_by_ids(
            self,
            app_ids: List[str],
            workspace_id: uuid.UUID
    ) -> List[App]:
        """根据ID列表获取应用

        Args:
            app_ids: 应用ID列表
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            List[App]: 应用列表
        """
        if not app_ids:
            return []

        # 转换字符串ID为UUID
        try:
            uuid_ids = [uuid.UUID(app_id) for app_id in app_ids]
        except ValueError:
            return []

        # 查询本工作空间的应用 + 分享给本工作空间的应用
        stmt = select(App).where(
            App.id.in_(uuid_ids),
            App.workspace_id == workspace_id
        )

        return list(self.db.scalars(stmt).all())

    # ==================== Agent 配置管理 ====================

    def update_agent_config(
            self,
            *,
            app_id: uuid.UUID,
            data: app_schema.AgentConfigUpdate,
            workspace_id: Optional[uuid.UUID] = None
    ) -> AgentConfig:
        """更新 Agent 配置

        Args:
            app_id: 应用ID
            data: 配置更新数据
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            AgentConfig: 更新后的配置对象

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用类型不支持或不在指定工作空间时
        """
        logger.info("更新 Agent 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type != "agent":
            raise BusinessException("只有 Agent 类型应用支持 Agent 配置", BizCode.APP_TYPE_NOT_SUPPORTED)

        self._validate_app_writable(app, workspace_id)

        stmt = select(AgentConfig).where(AgentConfig.app_id == app_id, AgentConfig.is_active.is_(True)).order_by(
            AgentConfig.updated_at.desc())
        agent_cfg: Optional[AgentConfig] = self.db.scalars(stmt).first()
        now = datetime.datetime.now()

        if not agent_cfg:
            agent_cfg = AgentConfig(
                id=uuid.uuid4(),
                app_id=app_id,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.db.add(agent_cfg)
            logger.debug("创建新的 Agent 配置", extra={"app_id": str(app_id)})

        # 转换为存储格式
        storage_data = AgentConfigConverter.to_storage_format(data)

        # 更新字段
        # if data.system_prompt is not None:
        agent_cfg.system_prompt = data.system_prompt
        # if data.default_model_config_id is not None:
        agent_cfg.default_model_config_id = data.default_model_config_id
        # if data.model_parameters is not None:
        agent_cfg.model_parameters = storage_data.get("model_parameters")
        # if data.knowledge_retrieval is not None:
        agent_cfg.knowledge_retrieval = storage_data.get("knowledge_retrieval")
        # if data.memory is not None:
        agent_cfg.memory = storage_data.get("memory")
        # if data.variables is not None:
        agent_cfg.variables = storage_data.get("variables", [])
        # if data.tools is not None:
        agent_cfg.tools = storage_data.get("tools", [])
        agent_cfg.skills = storage_data.get("skills", {})
        agent_cfg.features = storage_data.get("features", {})

        agent_cfg.updated_at = now

        self.db.commit()
        self.db.refresh(agent_cfg)

        logger.info("Agent 配置更新成功", extra={"app_id": str(app_id)})
        return agent_cfg

    def get_agent_config(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> AgentConfig:
        """获取 Agent 配置

        如果配置不存在，返回默认配置模板（不保存到数据库）

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            AgentConfig: Agent 配置对象（存在的配置或默认模板）

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用类型不支持或不可访问时
        """
        logger.debug("获取 Agent 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type != "agent":
            raise BusinessException("只有 Agent 类型应用支持 Agent 配置", BizCode.APP_TYPE_NOT_SUPPORTED)

        # 只读操作，允许访问共享应用
        self._validate_app_accessible(app, workspace_id)

        stmt = select(AgentConfig).where(
            AgentConfig.app_id == app_id,
            AgentConfig.is_active.is_(True)
        ).order_by(
            AgentConfig.updated_at.desc()
        )

        config = self.db.scalars(stmt).first()

        try:
            config_memory = config.memory
            if 'memory_content' in config_memory:
                config.memory['memory_config_id'] = config.memory.pop('memory_content')
        except:
            logger.debug("记忆配置不存在")
        if config:
            return config

        # 返回默认配置模板（不保存到数据库）
        logger.debug("配置不存在，返回默认模板", extra={"app_id": str(app_id)})
        return self._create_default_agent_config(app_id)

    def _create_default_agent_config(self, app_id: uuid.UUID) -> AgentConfig:
        """创建默认的 Agent 配置模板（不保存到数据库）

        Args:
            app_id: 应用ID

        Returns:
            AgentConfig: 默认配置对象
        """
        now = datetime.datetime.now()

        # 创建一个临时的配置对象，不添加到数据库
        default_config = AgentConfig(
            id=uuid.uuid4(),  # 临时ID
            app_id=app_id,
            system_prompt="你是一个专业的AI助手，你的职责是帮助用户解决问题。",
            default_model_config_id=None,
            model_parameters={
                "temperature": 0.7,
                "max_tokens": 2000,
                "top_p": 1.0,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "n": 1,
                "stop": None
            },
            knowledge_retrieval={
                "knowledge_bases": [],
                "merge_strategy": "weighted"
            },
            memory={
                "enabled": True,
                "memory_config_id": None,
                "max_history": 10
            },
            variables=[],
            tools=[],
            skills=[],
            features={},
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        return default_config

    def get_workflow_config(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> WorkflowConfig:
        """获取 workflow 配置

        如果配置不存在，返回默认配置模板（不保存到数据库）

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            WorkflowConfig: Workflow 配置对象（存在的配置或默认模板）

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用类型不支持或不可访问时
        """
        logger.debug("获取 Workflow 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type != AppType.WORKFLOW:
            raise BusinessException("只有 Workflow 类型应用支持 Workflow 配置", BizCode.APP_TYPE_NOT_SUPPORTED)

        # 只读操作，允许访问共享应用
        self._validate_app_accessible(app, workspace_id)
        repo = WorkflowConfigRepository(self.db)
        config = repo.get_by_app_id(app_id)
        if config:
            return config

        # 返回默认配置模板（不保存到数据库）
        logger.debug("配置不存在，返回默认模板", extra={"app_id": str(app_id)})
        return self._create_default_workflow_config(app_id)

    def update_workflow_config(
            self,
            *,
            app_id: uuid.UUID,
            data: WorkflowConfigUpdate,
            workspace_id: Optional[uuid.UUID] = None
    ) -> WorkflowConfig:
        """更新 Workflow 配置（全量更新）

        Args:
            app_id: 应用ID
            data: 配置更新数据（全量数据）
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            WorkflowConfig: 更新后的配置对象

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用类型不支持或不在指定工作空间时
        """
        logger.info("更新 Workflow 配置", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)

        if app.type != AppType.WORKFLOW:
            raise BusinessException("只有 Workflow 类型应用支持 Workflow 配置", BizCode.APP_TYPE_NOT_SUPPORTED)

        self._validate_app_writable(app, workspace_id)

        # 获取现有配置
        repo = WorkflowConfigRepository(self.db)
        workflow_cfg = repo.get_by_app_id(app_id)
        now = datetime.datetime.now()

        if not workflow_cfg:
            # 如果配置不存在，创建新配置
            workflow_cfg = WorkflowConfig(
                id=uuid.uuid4(),
                app_id=app_id,
                nodes=[node.model_dump() for node in data.nodes] if data.nodes else [],
                edges=[edge.model_dump() for edge in data.edges] if data.edges else [],
                variables=[var.model_dump() for var in data.variables] if data.variables else [],
                execution_config=data.execution_config.model_dump() if data.execution_config else {},
                triggers=[trigger.model_dump() for trigger in data.triggers] if data.triggers else [],
                is_active=True,
                created_at=now,
                updated_at=now
            )
            self.db.add(workflow_cfg)
            logger.debug("创建新的 Workflow 配置", extra={"app_id": str(app_id)})
        else:
            # 全量更新现有配置
            workflow_cfg.nodes = [node.model_dump() for node in data.nodes] if data.nodes else []
            workflow_cfg.edges = [edge.model_dump() for edge in data.edges] if data.edges else []
            workflow_cfg.variables = [var.model_dump() for var in data.variables] if data.variables else []
            workflow_cfg.execution_config = data.execution_config.model_dump() if data.execution_config else {}
            workflow_cfg.triggers = [trigger.model_dump() for trigger in data.triggers] if data.triggers else []
            workflow_cfg.updated_at = now

        self.db.commit()
        self.db.refresh(workflow_cfg)

        logger.info("Workflow 配置更新成功", extra={"app_id": str(app_id)})
        return workflow_cfg

    def _create_default_workflow_config(self, app_id: uuid.UUID) -> WorkflowConfig:
        """创建默认的 workflow 配置模板（不保存到数据库）

        使用 template_loader 加载 simple_qa 模板作为默认配置

        Args:
            app_id: 应用ID

        Returns:
            WorkflowConfig: 默认配置对象
        """
        from app.core.workflow.template_loader import load_workflow_template

        now = datetime.datetime.now()

        # 使用 template_loader 加载 simple_qa 模板
        template_data = load_workflow_template('simple_qa')

        if not template_data:
            # 如果模板加载失败，返回最小化配置
            logger.warning(
                "无法加载默认工作流模板，使用最小化配置",
                extra={"app_id": str(app_id)}
            )
            template_data = {
                'nodes': [
                    {'id': 'start', 'type': 'start', 'name': '开始'},
                    {'id': 'end', 'type': 'end', 'name': '结束'}
                ],
                'edges': [
                    {'source': 'start', 'target': 'end'}
                ],
                'variables': [],
                'execution_config': {
                    'max_execution_time': 300,
                    'max_iterations': 10
                },
                'triggers': []
            }

        # 转换为 WorkflowConfig 格式
        default_config = WorkflowConfig(
            id=uuid.uuid4(),
            app_id=app_id,
            nodes=template_data.get('nodes', []),
            edges=template_data.get('edges', []),
            variables=template_data.get('variables', []),
            execution_config=template_data.get('execution_config', {}),
            triggers=template_data.get('triggers', []),
            is_active=True,
            created_at=now,
            updated_at=now
        )

        return default_config

    # ==================== 记忆配置提取方法 ====================

    def _extract_memory_config_id(
            self,
            app_type: str,
            config: Dict[str, Any]
    ) -> Tuple[Optional[uuid.UUID], bool]:
        """从发布配置中提取 memory_config_id（委托给 MemoryConfigService）
        
        Args:
            app_type: 应用类型 (agent, workflow, multi_agent)
            config: 发布配置字典
            
        Returns:
            Tuple[Optional[uuid.UUID], bool]: (memory_config_id, is_legacy_int)
                - memory_config_id: 提取的配置ID，如果不存在或为旧格式则返回 None
                - is_legacy_int: 是否检测到旧格式 int 数据，需要回退到工作空间默认配置
        """
        from app.services.memory_config_service import MemoryConfigService

        service = MemoryConfigService(self.db)
        return service.extract_memory_config_id(app_type, config)

    def _get_workspace_default_memory_config_id(
            self,
            workspace_id: uuid.UUID
    ) -> Optional[uuid.UUID]:
        """获取工作空间的默认记忆配置ID
        
        Args:
            workspace_id: 工作空间ID
            
        Returns:
            Optional[uuid.UUID]: 默认记忆配置ID，如果不存在则返回 None
        """
        from app.services.memory_config_service import MemoryConfigService

        service = MemoryConfigService(self.db)
        config = service.get_workspace_default_config(workspace_id)

        if not config:
            logger.warning(
                f"工作空间没有可用的记忆配置: workspace_id={workspace_id}"
            )
            return None

        return config.config_id

    def _update_endusers_memory_config_by_workspace(
            self,
            workspace_id: uuid.UUID,
            memory_config_id: uuid.UUID
    ) -> int:
        """批量更新应用下所有终端用户的 memory_config_id
        
        Args:
            workspace_id: 工作空间ID
            memory_config_id: 新的记忆配置ID
            
        Returns:
            int: 更新的终端用户数量
        """
        from app.repositories.end_user_repository import EndUserRepository

        repo = EndUserRepository(self.db)
        updated_count = repo.batch_update_memory_config_id_by_workspace(
            workspace_id=workspace_id,
            memory_config_id=memory_config_id
        )

        return updated_count

    # ==================== 应用发布管理 ====================

    def publish(
            self,
            *,
            app_id: uuid.UUID,
            publisher_id: uuid.UUID,
            version_name: str,
            workspace_id: Optional[uuid.UUID] = None,
            release_notes: Optional[str] = None
    ) -> AppRelease:
        """发布应用（创建不可变快照）

        Args:
            app_id: 应用ID
            publisher_id: 发布者用户ID
            workspace_id: 工作空间ID（用于权限验证）
            release_notes: 版本说明

        Returns:
            AppRelease: 发布版本对象

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用缺少配置或不在指定工作空间时
        """
        logger.info("发布应用", extra={"app_id": str(app_id), "publisher_id": str(publisher_id)})

        app = self._get_app_or_404(app_id)
        # 检查应用归属
        self._validate_workspace_access(app, workspace_id)

        # 构建快照配置
        config: Dict[str, Any] = {}
        default_model_config_id = None

        if app.type == AppType.AGENT:
            stmt = select(AgentConfig).where(AgentConfig.app_id == app_id, AgentConfig.is_active.is_(True)).order_by(
                AgentConfig.updated_at.desc())
            agent_cfg = self.db.scalars(stmt).first()
            if not agent_cfg:
                raise BusinessException("Agent 应用缺少配置，无法发布", BizCode.AGENT_CONFIG_MISSING)

            miss_params = []
            if agent_cfg.default_model_config_id is None:
                miss_params.append("model config")

            if agent_cfg.memory.get("enabled") and not agent_cfg.memory.get("memory_config_id"):
                miss_params.append("memory config")
            if miss_params:
                raise BusinessException(f"{', '.join(miss_params)} is required")

            config = {
                "system_prompt": agent_cfg.system_prompt,
                "model_parameters": model_parameters_to_dict(agent_cfg.model_parameters),
                "knowledge_retrieval": agent_cfg.knowledge_retrieval,
                "memory": agent_cfg.memory,
                "variables": agent_cfg.variables or [],
                "tools": agent_cfg.tools or [],
                "skills": agent_cfg.skills or {},
            }
            # config = AgentConfigConverter.from_storage_format(agent_cfg)
            default_model_config_id = agent_cfg.default_model_config_id
        elif app.type == AppType.MULTI_AGENT:
            # 1. 获取多智能体配置
            stmt = (
                select(MultiAgentConfig)
                .where(
                    MultiAgentConfig.app_id == app_id,
                    MultiAgentConfig.is_active.is_(True)
                )
                .order_by(MultiAgentConfig.updated_at.desc())
            )
            multi_agent_cfg = self.db.scalars(stmt).first()
            if not multi_agent_cfg:
                raise BusinessException("多 Agent 应用缺少有效配置，无法发布", BizCode.AGENT_CONFIG_MISSING)

            # 2. 检查配置完整性
            self._check_multi_agent_config(app_id)

            # 3. 获取主 Agent 的模型配置 ID
            default_model_config_id = multi_agent_cfg.default_model_config_id

            # 4. 构建配置快照

            config = {
                "model_parameters": model_parameters_to_dict(multi_agent_cfg.model_parameters),
                "master_agent_id": str(multi_agent_cfg.master_agent_id),
                "orchestration_mode": multi_agent_cfg.orchestration_mode,
                "sub_agents": multi_agent_cfg.sub_agents,
                "routing_rules": multi_agent_cfg.routing_rules,
                "execution_config": multi_agent_cfg.execution_config,
                "aggregation_strategy": multi_agent_cfg.aggregation_strategy,
            }

            logger.info(
                "多智能体应用发布配置准备完成",
                extra={
                    "app_id": str(app_id),
                    "default_model_config_id": str(default_model_config_id),
                    "sub_agent_count": len(multi_agent_cfg.sub_agents) if multi_agent_cfg.sub_agents else 0,
                    "orchestration_mode": multi_agent_cfg.orchestration_mode
                }
            )
        elif app.type == AppType.WORKFLOW:
            service = WorkflowService(self.db)
            workflow_cfg = service.get_workflow_config(app_id)
            if not workflow_cfg:
                raise BusinessException("应用缺少有效配置，无法发布", BizCode.CONFIG_MISSING)

            config = {
                "id": str(workflow_cfg.id),
                "nodes": workflow_cfg.nodes,
                "edges": workflow_cfg.edges,
                "variables": workflow_cfg.variables,
                "execution_config": workflow_cfg.execution_config,
                "triggers": workflow_cfg.triggers
            }

            is_valid, errors = WorkflowValidator.validate_for_publish(config)
            if not is_valid:
                raise BusinessException(f"应用缺少有效配置，无法发布, errors:{','.join(errors)}", BizCode.CONFIG_MISSING)
            logger.info(
                "应用发布配置准备完成"
            )

        now = datetime.datetime.now()
        version = self._get_next_version(app_id)

        release = AppRelease(
            id=uuid.uuid4(),
            app_id=app_id,
            version=version,
            version_name=version_name,
            release_notes=release_notes,
            name=app.name,
            description=app.description,
            icon=app.icon,
            icon_type=app.icon_type,
            type=app.type,
            visibility=app.visibility,
            config=config,
            default_model_config_id=default_model_config_id,
            published_by=publisher_id,
            published_at=now,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.db.add(release)
        self.db.flush()  # 先 flush，确保 release 已插入数据库

        # 提取记忆配置ID并更新终端用户
        memory_config_id, is_legacy_int = self._extract_memory_config_id(app.type, config)

        # 如果检测到旧格式 int 数据，回退到工作空间默认配置
        if is_legacy_int and not memory_config_id:
            memory_config_id = self._get_workspace_default_memory_config_id(app.workspace_id)
            if memory_config_id:
                logger.info(
                    f"发布时使用工作空间默认记忆配置（旧数据兼容）: app_id={app_id}, "
                    f"workspace_id={app.workspace_id}, memory_config_id={memory_config_id}"
                )

        if memory_config_id:
            app = self.db.query(App).filter(App.id == app_id).first()
            if app:
                updated_count = self._update_endusers_memory_config_by_workspace(
                    app.workspace_id, memory_config_id
                )
                logger.info(
                    f"发布时更新终端用户记忆配置: app_id={app_id}, workspace_id={app.workspace_id}, "
                    f"memory_config_id={memory_config_id}, updated_count={updated_count}"
                )

        # 更新当前发布版本指针
        app.current_release_id = release.id
        app.status = AppStatus.ACTIVE
        app.updated_at = now

        self.db.commit()
        self.db.refresh(release)

        logger.info(
            "应用发布成功",
            extra={"app_id": str(app_id), "version": version, "release_id": str(release.id)}
        )
        return release

    def get_current_release(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> Optional[AppRelease]:
        """获取当前发布版本

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            Optional[AppRelease]: 当前发布版本，如果未发布则返回 None

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不可访问时
        """
        logger.debug("获取当前发布版本", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        # 只读操作，允许访问共享应用
        self._validate_app_accessible(app, workspace_id)

        if not app.current_release_id:
            return None

        return self.db.get(AppRelease, app.current_release_id)

    def list_releases(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> List[AppRelease]:
        """列出应用的所有发布版本（倒序）

        Args:
            app_id: 应用ID
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            List[AppRelease]: 发布版本列表

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不可访问时
        """
        logger.debug("列出发布版本", extra={"app_id": str(app_id)})

        app = self._get_app_or_404(app_id)
        # 只读操作，允许访问共享应用
        self._validate_app_accessible(app, workspace_id)

        stmt = (
            select(AppRelease)
            .where(AppRelease.app_id == app_id, AppRelease.is_active.is_(True))
            .order_by(AppRelease.version.desc())
        )
        return list(self.db.scalars(stmt).all())

    def rollback(
            self,
            *,
            app_id: uuid.UUID,
            version: int,
            workspace_id: Optional[uuid.UUID] = None
    ) -> AppRelease:
        """回滚到指定版本

        Args:
            app_id: 应用ID
            version: 目标版本号
            workspace_id: 工作空间ID（用于权限验证）

        Returns:
            AppRelease: 回滚到的版本对象

        Raises:
            ResourceNotFoundException: 当应用或版本不存在时
            BusinessException: 当应用不在指定工作空间时
        """
        logger.info("回滚应用", extra={"app_id": str(app_id), "version": version})

        app = self._get_app_or_404(app_id)
        self._validate_app_accessible(app, workspace_id)

        stmt = select(AppRelease).where(
            AppRelease.app_id == app_id,
            AppRelease.version == version
        )
        release = self.db.scalars(stmt).first()

        if not release:
            logger.warning(
                "发布版本不存在",
                extra={"app_id": str(app_id), "version": version}
            )
            raise ResourceNotFoundException("发布版本", f"app_id={app_id}, version={version}")

        # 提取记忆配置ID并更新终端用户
        memory_config_id, is_legacy_int = self._extract_memory_config_id(release.type, release.config)

        # 如果检测到旧格式 int 数据，回退到工作空间默认配置
        if is_legacy_int and not memory_config_id:
            memory_config_id = self._get_workspace_default_memory_config_id(app.workspace_id)
            if memory_config_id:
                logger.info(
                    f"回滚时使用工作空间默认记忆配置（旧数据兼容）: app_id={app_id}, "
                    f"workspace_id={app.workspace_id}, memory_config_id={memory_config_id}"
                )

        if memory_config_id:
            updated_count = self._update_endusers_memory_config(app_id, memory_config_id)
            logger.info(
                f"回滚时更新终端用户记忆配置: app_id={app_id}, version={version}, "
                f"memory_config_id={memory_config_id}, updated_count={updated_count}"
            )

        app.current_release_id = release.id
        app.updated_at = datetime.datetime.now()

        self.db.commit()
        self.db.refresh(release)

        logger.info(
            "应用回滚成功",
            extra={"app_id": str(app_id), "version": version, "release_id": str(release.id)}
        )
        return release

    # ==================== 应用分享功能 ====================

    def share_app(
            self,
            *,
            app_id: uuid.UUID,
            target_workspace_ids: List[uuid.UUID],
            user_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None,
            permission: str = "readonly"
    ) -> list[AppShare]:
        """分享应用到其他工作空间

        Args:
            app_id: 应用ID
            target_workspace_ids: 目标工作空间ID列表
            user_id: 分享者用户ID
            workspace_id: 当前工作空间ID（用于权限验证）

        Returns:
            List[AppShare]: 创建的分享记录列表

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不在指定工作空间或目标工作空间无效时
        """

        logger.info(
            "分享应用",
            extra={
                "app_id": str(app_id),
                "target_workspaces": [str(wid) for wid in target_workspace_ids],
                "user_id": str(user_id)
            }
        )

        # 1. 验证应用
        app = self._get_app_or_404(app_id)
        self._validate_workspace_access(app, workspace_id)

        # 仅允许 agent 和 workflow 类型共享，multi_agent 不支持
        from app.models.app_model import AppType
        if app.type == AppType.MULTI_AGENT:
            raise BusinessException(
                "集群 Agent 不支持共享应用功能",
                BizCode.INVALID_PARAMETER
            )

        # 2. 验证目标工作空间
        for target_ws_id in target_workspace_ids:
            target_ws = self.db.get(Workspace, target_ws_id)
            if not target_ws:
                raise ResourceNotFoundException("工作空间", str(target_ws_id))

            # 不能分享给自己的工作空间
            if target_ws_id == app.workspace_id:
                raise BusinessException(
                    "不能分享应用到自己的工作空间",
                    BizCode.INVALID_PARAMETER
                )

        # 3. 创建分享记录
        now = datetime.datetime.now()
        shares = []

        for target_ws_id in target_workspace_ids:
            # 检查是否已经分享过
            stmt = select(AppShare).where(
                AppShare.source_app_id == app_id,
                AppShare.target_workspace_id == target_ws_id,
                AppShare.is_active.is_(True)
            )
            existing_share = self.db.scalars(stmt).first()

            if existing_share:
                logger.debug(
                    "应用已分享到该工作空间，跳过",
                    extra={"app_id": str(app_id), "target_workspace_id": str(target_ws_id)}
                )
                shares.append(existing_share)
                continue

            # 创建新的分享记录
            share = AppShare(
                id=uuid.uuid4(),
                source_app_id=app_id,
                source_workspace_id=app.workspace_id,
                target_workspace_id=target_ws_id,
                shared_by=user_id,
                permission=permission,
                created_at=now,
                updated_at=now
            )
            self.db.add(share)
            shares.append(share)

            logger.debug(
                "创建分享记录",
                extra={"app_id": str(app_id), "target_workspace_id": str(target_ws_id)}
            )

        self.db.commit()

        logger.info(
            "应用分享成功",
            extra={
                "app_id": str(app_id),
                "shared_count": len(shares),
                "app_name": app.name
            }
        )

        return shares

    def unshare_app(
            self,
            *,
            app_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> None:
        """取消应用分享

        Args:
            app_id: 应用ID
            target_workspace_id: 目标工作空间ID
            workspace_id: 当前工作空间ID（用于权限验证）

        Raises:
            ResourceNotFoundException: 当应用或分享记录不存在时
            BusinessException: 当应用不在指定工作空间时
        """
        from app.models import AppShare

        logger.info(
            "取消应用分享",
            extra={
                "app_id": str(app_id),
                "target_workspace_id": str(target_workspace_id)
            }
        )

        # 1. 验证应用
        app = self._get_app_or_404(app_id)
        self._validate_workspace_access(app, workspace_id)

        # 2. 查找分享记录
        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.target_workspace_id == target_workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        if not share:
            logger.warning(
                "分享记录不存在",
                extra={"app_id": str(app_id), "target_workspace_id": str(target_workspace_id)}
            )
            raise ResourceNotFoundException(
                "分享记录",
                f"app_id={app_id}, target_workspace_id={target_workspace_id}"
            )

        # 3. 逻辑删除分享记录
        share.is_active = False
        self.db.commit()

        logger.info(
            "应用分享已取消",
            extra={"app_id": str(app_id), "target_workspace_id": str(target_workspace_id)}
        )

    def unshare_all_apps_to_workspace(
            self,
            *,
            target_workspace_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> int:
        """Cancel all app shares from current workspace to a target workspace.

        Args:
            target_workspace_id: Target workspace ID to cancel all shares to
            workspace_id: Current workspace ID (source)

        Returns:
            Number of share records deleted
        """
        from app.models import AppShare

        logger.info(
            "取消对目标工作空间的所有应用分享",
            extra={"target_workspace_id": str(target_workspace_id), "workspace_id": str(workspace_id)}
        )

        # Query active records first for reliable count
        id_stmt = select(AppShare.id).where(
            AppShare.source_workspace_id == workspace_id,
            AppShare.target_workspace_id == target_workspace_id,
            AppShare.is_active.is_(True)
        )
        ids = list(self.db.scalars(id_stmt).all())
        count = len(ids)

        if ids:
            # Soft delete: mark as inactive
            from sqlalchemy import update as sa_update
            self.db.execute(
                sa_update(AppShare).where(AppShare.id.in_(ids)).values(is_active=False)
            )
            self.db.commit()

        logger.info("已取消分享记录数", extra={"count": count})
        return count

    def list_app_shares(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> List[AppShare]:
        """列出应用的所有分享记录

        Args:
            app_id: 应用ID
            workspace_id: 当前工作空间ID（用于权限验证）

        Returns:
            List[AppShare]: 分享记录列表

        Raises:
            ResourceNotFoundException: 当应用不存在时
            BusinessException: 当应用不在指定工作空间时
        """
        from app.models import AppShare

        logger.debug("列出应用分享记录", extra={"app_id": str(app_id)})

        # 验证应用
        app = self._get_app_or_404(app_id)
        self._validate_workspace_access(app, workspace_id)

        # 查询分享记录
        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.is_active.is_(True)
        ).order_by(AppShare.created_at.desc())

        shares = list(self.db.scalars(stmt).all())

        logger.debug(
            "应用分享记录查询完成",
            extra={"app_id": str(app_id), "count": len(shares)}
        )

        return shares

    def remove_shared_app(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> None:
        """被共享者从自己的工作空间移除共享应用

        只删除共享记录，不影响源应用。

        Args:
            app_id: 应用ID
            workspace_id: 当前工作空间ID（被共享的目标工作空间）

        Raises:
            ResourceNotFoundException: 当共享记录不存在时
        """
        from app.models import AppShare

        logger.info(
            "移除共享应用",
            extra={"app_id": str(app_id), "workspace_id": str(workspace_id)}
        )

        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        if not share:
            raise ResourceNotFoundException(
                "共享记录",
                f"app_id={app_id}, workspace_id={workspace_id}"
            )

        # Soft delete
        share.is_active = False
        self.db.commit()

        logger.info(
            "共享应用已移除",
            extra={"app_id": str(app_id), "workspace_id": str(workspace_id)}
        )

    def remove_all_shared_apps_from_workspace(
            self,
            *,
            source_workspace_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> int:
        """Remove all shared apps from a specific source workspace.

        Args:
            source_workspace_id: The workspace that shared the apps
            workspace_id: Current workspace ID (recipient)

        Returns:
            Number of share records deleted
        """
        from app.models import AppShare

        logger.info(
            "批量移除来源工作空间的共享应用",
            extra={"source_workspace_id": str(source_workspace_id), "workspace_id": str(workspace_id)}
        )

        # Query active records for reliable count, then soft delete
        id_stmt = select(AppShare.id).where(
            AppShare.source_workspace_id == source_workspace_id,
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active.is_(True)
        )
        ids = list(self.db.scalars(id_stmt).all())
        count = len(ids)

        if ids:
            from sqlalchemy import update as sa_update
            self.db.execute(
                sa_update(AppShare).where(AppShare.id.in_(ids)).values(is_active=False)
            )
            self.db.commit()

        logger.info("已移除共享记录数", extra={"count": count})
        return count

    def list_my_shared_out(
            self,
            *,
            workspace_id: uuid.UUID
    ) -> List[AppShare]:
        """列出本工作空间主动分享出去的所有记录（我的共享）

        Returns:
            List[AppShare]: 分享记录列表，含源应用信息
        """
        from app.models import AppShare

        stmt = (
            select(AppShare)
            .where(
                AppShare.source_workspace_id == workspace_id,
                AppShare.is_active.is_(True)
            )
            .order_by(AppShare.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())
    def update_share_permission(
            self,
            *,
            app_id: uuid.UUID,
            target_workspace_id: uuid.UUID,
            permission: str,
            workspace_id: Optional[uuid.UUID] = None
    ) -> "AppShare":
        """更新共享权限（readonly <-> editable）

        Args:
            app_id: 应用ID
            target_workspace_id: 目标工作空间ID
            permission: 新权限值 readonly | editable
            workspace_id: 当前工作空间ID（用于权限验证）

        Returns:
            AppShare: 更新后的共享记录
        """
        from app.models import AppShare

        if permission not in ("readonly", "editable"):
            raise BusinessException("权限值无效，只允许 readonly 或 editable", BizCode.INVALID_PARAMETER)

        app = self._get_app_or_404(app_id)
        self._validate_workspace_access(app, workspace_id)

        stmt = select(AppShare).where(
            AppShare.source_app_id == app_id,
            AppShare.target_workspace_id == target_workspace_id,
            AppShare.is_active.is_(True)
        )
        share = self.db.scalars(stmt).first()

        if not share:
            raise ResourceNotFoundException(
                "共享记录",
                f"app_id={app_id}, target_workspace_id={target_workspace_id}"
            )

        share.permission = permission
        share.updated_at = datetime.datetime.now()
        self.db.commit()
        self.db.refresh(share)

        logger.info(
            "共享权限已更新",
            extra={"app_id": str(app_id), "target_workspace_id": str(target_workspace_id), "permission": permission}
        )
        return share


# ==================== 向后兼容的函数接口 ====================
# 保留函数接口以兼容现有代码，但内部使用服务类

def create_app(db: Session, *, user_id: uuid.UUID, workspace_id: uuid.UUID, data: app_schema.AppCreate) -> App:
    """创建应用（向后兼容接口）"""
    service = AppService(db)
    return service.create_app(user_id=user_id, workspace_id=workspace_id, data=data)


def update_app(db: Session, *, app_id: uuid.UUID, data: app_schema.AppUpdate,
               workspace_id: uuid.UUID | None = None) -> App:
    """更新应用（向后兼容接口）"""
    service = AppService(db)
    return service.update_app(app_id=app_id, data=data, workspace_id=workspace_id)


def delete_app(db: Session, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None = None) -> None:
    """删除应用（向后兼容接口）"""
    service = AppService(db)
    return service.delete_app(app_id=app_id, workspace_id=workspace_id)


def update_agent_config(db: Session, *, app_id: uuid.UUID, data: app_schema.AgentConfigUpdate,
                        workspace_id: uuid.UUID | None = None) -> AgentConfig:
    """更新 Agent 配置（向后兼容接口）"""
    service = AppService(db)
    return service.update_agent_config(app_id=app_id, data=data, workspace_id=workspace_id)


def update_workflow_config(db: Session, *, app_id: uuid.UUID, data: WorkflowConfigUpdate,
                           workspace_id: uuid.UUID | None = None) -> WorkflowConfig:
    """更新 Agent 配置（向后兼容接口）"""
    service = AppService(db)
    return service.update_workflow_config(app_id=app_id, data=data, workspace_id=workspace_id)


def get_agent_config(db: Session, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None = None) -> AgentConfig:
    """获取 Agent 配置（向后兼容接口）

    如果配置不存在，返回默认配置模板
    """
    service = AppService(db)
    return service.get_agent_config(app_id=app_id, workspace_id=workspace_id)


def get_workflow_config(db: Session, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None = None) -> WorkflowConfig:
    """获取 Agent 配置（向后兼容接口）

    如果配置不存在，返回默认配置模板
    """
    service = AppService(db)
    return service.get_workflow_config(app_id=app_id, workspace_id=workspace_id)


def publish(db: Session, *, app_id: uuid.UUID, publisher_id: uuid.UUID, workspace_id: uuid.UUID | None = None,
            version_name: str, release_notes: Optional[str] = None) -> AppRelease:
    """发布应用（向后兼容接口）"""
    service = AppService(db)
    return service.publish(app_id=app_id, publisher_id=publisher_id, version_name=version_name,
                           workspace_id=workspace_id, release_notes=release_notes)


def get_current_release(
        db: Session,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None
) -> Optional[AppRelease]:
    """获取当前发布版本（向后兼容接口）"""
    service = AppService(db)
    return service.get_current_release(app_id=app_id, workspace_id=workspace_id)


def list_releases(db: Session, *, app_id: uuid.UUID, workspace_id: uuid.UUID | None = None) -> List[AppRelease]:
    """列出发布版本（向后兼容接口）"""
    service = AppService(db)
    return service.list_releases(app_id=app_id, workspace_id=workspace_id)


def rollback(db: Session, *, app_id: uuid.UUID, version: int, workspace_id: uuid.UUID | None = None) -> AppRelease:
    """回滚应用（向后兼容接口）"""
    service = AppService(db)
    return service.rollback(app_id=app_id, version=version, workspace_id=workspace_id)


def list_apps(
        db: Session,
        *,
        workspace_id: uuid.UUID,
        type: Optional[str] = None,
        visibility: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        include_shared: bool = True,
        shared_only: bool = False,
        page: int = 1,
        pagesize: int = 10,
) -> Tuple[List[App], int]:
    """列出应用（向后兼容接口）"""
    service = AppService(db)
    return service.list_apps(
        workspace_id=workspace_id,
        type=type,
        visibility=visibility,
        status=status,
        search=search,
        include_shared=include_shared,
        shared_only=shared_only,
        page=page,
        pagesize=pagesize,
    )


def get_apps_by_ids(
        db: Session,
        app_ids: List[str],
        workspace_id: uuid.UUID
) -> List[App]:
    """根据ID列表获取应用（向后兼容接口）"""
    service = AppService(db)
    return service.get_apps_by_ids(app_ids, workspace_id)


# ==================== 依赖注入函数 ====================

def get_app_service(
        db: Annotated[Session, Depends(get_db)]
) -> AppService:
    """获取工作流服务（依赖注入）"""
    return AppService(db)
