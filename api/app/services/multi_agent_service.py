"""多 Agent 配置管理服务"""
import uuid
import json
from typing import AsyncGenerator, Optional, List, Tuple, Any, Annotated, Dict

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db import get_db, get_async_db_context
from app.models import MultiAgentConfig, App, AgentConfig
from app.schemas.multi_agent_schema import (
    MultiAgentConfigCreate,
    MultiAgentConfigUpdate,
    MultiAgentRunRequest
)
from app.services.model_service import ModelApiKeyService, ModelConfigService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.core.exceptions import ResourceNotFoundException, BusinessException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.models import AppRelease
from app.services.multi_agent_release_resolver import (
    describe_release_state,
    resolve_release_for_save,
)
from app.models.annotation_model import HitLogSource

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
    elif isinstance(obj, str):
        # PostgreSQL 的 text/jsonb 禁止 NUL(U+0000)。节点输出（如知识检索回传的
        # chunk 原文）可能混入该字符，原样入库即抛 UntranslatableCharacterError
        # (22P05 unsupported Unicode escape sequence)。本函数用于写库前净化。
        return obj.replace("\x00", "") if "\x00" in obj else obj
    elif isinstance(obj, dict):
        return {k: convert_uuids_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuids_to_str(item) for item in obj]
    else:
        return obj


class MultiAgentService:
    """多 Agent 配置管理服务"""

    # 同一轮回复的最大版本数（含原始回复），与 WorkflowService.MAX_REGENERATE_VERSIONS 对齐
    MAX_REGENERATE_VERSIONS = 5

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def _uses_async_session(self) -> bool:
        return isinstance(self.db, AsyncSession)

    async def _get_last_current_assistant_id(self, conversation_id) -> Optional[uuid.UUID]:
        """本轮提问之前，会话里最后一条 is_current 的 assistant 消息 ID。

        用作本轮 user 消息的 parent_message_id（user 接在用户当前所处的版本之后），
        使 _load_branch_chain 能按 parent 精确隔离各版本的后续对话。
        必须在本轮消息入队之前调用；查询失败降级为 None（等同旧行为），不影响落库。
        """
        if not conversation_id:
            return None
        try:
            from app.models import Message
            conv_uuid = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(Message.id).where(
                        Message.conversation_id == conv_uuid,
                        Message.role == "assistant",
                        Message.is_current.is_(True),
                        Message.is_deleted.is_not(True),
                    ).order_by(Message.created_at.desc()).limit(1)
                )
                return result.scalar_one_or_none()
        except Exception as e:
            logger.warning("查询上一条当前 assistant 消息失败，user 消息不记录 parent", extra={"error": str(e)})
            return None

    async def _conversation_has_messages(self, conversation_id: uuid.UUID) -> bool:
        """新会话判定（对齐 AppChatService._conversation_has_messages）。"""
        if self._uses_async_session():
            from app.models import Message
            result = await self.db.execute(
                select(Message.id)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.created_at)
                .limit(1)
            )
            return result.first() is not None
        from app.services.conversation_service import ConversationService
        existing_messages = ConversationService(self.db).message_repo.get_message_by_conversation_id(
            conversation_id=conversation_id,
            limit=1,
        )
        return len(existing_messages) > 0

    async def _cluster_annotation_hit(
        self,
        config,
        message: str,
        conversation_id,
        message_id,
        user_message_id,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        source: str = HitLogSource.CONSOLE,
    ) -> Optional[dict]:
        """集群标注命中并落库 user/assistant（用于 run/run_stream 首轮对话）。

        委托 AppChatService 同一份实现（兼容同步/异步 Session），未配置/未启用/
        未命中/任何异常均返回 None，调用方继续走正常编排，不阻断对话。
        """
        from app.services.app_chat_service import AppChatService
        return await AppChatService(self.db)._cluster_annotation_hit(
            config, message, conversation_id, message_id, user_message_id,
            storage_type=storage_type, user_rag_memory_id=user_rag_memory_id, source=source,
        )

    async def _build_cluster_files_persist(self, files, orchestrator) -> tuple:
        """集群本轮附件落库内容（files_meta, history_files），委托 AppChatService 同一份实现，
        与分享端集群同口径（元数据查询走独立 async session，不依赖 self.db 类型）。"""
        from app.services.app_chat_service import AppChatService
        return await AppChatService(self.db)._build_cluster_files_persist(files, orchestrator)

    async def _match_cluster_annotation(self, app_id, message: str, source: str = HitLogSource.CONSOLE) -> Optional[dict]:
        """仅做集群标注匹配（不落库消息），用于重新生成场景——消息版本由 regenerate 统一落库。"""
        from app.services.app_chat_service import AppChatService
        return await AppChatService(self.db)._check_annotation_match_async(app_id, message, source=source)

    async def _resolve_cluster_opening(
        self,
        orchestrator: MultiAgentOrchestrator,
        conversation_id: uuid.UUID,
        variables: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[str], List[str]]:
        """集群新会话开场白（试运行入口）。仅 features.opening_statement 开启时才查库。

        向后兼容：存量集群没有 features → 直接返回 (None, [])，不产生任何额外查询；
        失败降级为无开场白，不阻断对话。
        """
        features = getattr(orchestrator, "_features", None) or {}
        opening_cfg = features.get("opening_statement")
        if not (isinstance(opening_cfg, dict) and opening_cfg.get("enabled") and opening_cfg.get("statement")):
            return None, []
        try:
            is_new_conversation = not await self._conversation_has_messages(conversation_id)
            from app.services.cluster_chat_features import resolve_opening

            return resolve_opening(features, variables, is_new_conversation)
        except Exception as e:  # noqa: BLE001 - 开场白是增量能力
            logger.warning(f"集群开场白解析失败（已跳过）: {e}")
            return None, []

    async def _release_db_connection(self) -> None:
        """Release the underlying DB connection back to the pool before LLM streaming."""
        try:
            if self._uses_async_session():
                await self.db.close()
            else:
                self.db.close()
        except Exception as e:
            logger.warning(f"Failed to release DB connection: {e}")

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
                MultiAgentConfig.is_active.is_(True)
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

        # 处理 supervisor_config（主管即 Agent；None=裸主管现状，列保持 NULL）
        if getattr(data, "supervisor_config", None) is None:
            supervisor_config_data = None
        elif isinstance(data.supervisor_config, dict):
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config)
        else:
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config.model_dump())

        config = MultiAgentConfig(
            app_id=app_id,
            master_agent_id=data.master_agent_id,
            master_agent_name=data.master_agent_name,
            orchestration_mode=data.orchestration_mode,
            sub_agents=sub_agents_data,
            routing_rules=routing_rules_data,
            execution_config=execution_config_data,
            supervisor_config=supervisor_config_data,
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
                MultiAgentConfig.is_active.is_(True)
            )
            .order_by(MultiAgentConfig.updated_at.desc())
        ).first()

    async def get_config_async(self, app_id: uuid.UUID) -> Optional[MultiAgentConfig]:
        if isinstance(self.db, AsyncSession):
            result = await self.db.execute(
                select(MultiAgentConfig)
                .where(
                    MultiAgentConfig.app_id == app_id,
                    MultiAgentConfig.is_active.is_(True)
                )
                .order_by(MultiAgentConfig.updated_at.desc())
            )
            return result.scalars().first()
        return self.get_config(app_id)

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

        #兼容代码
        if not config.default_model_config_id:
            master_release = self.db.get(AppRelease, config.master_agent_id)
            config.default_model_config_id = master_release.default_model_config_id if master_release else None

        # 转换 sub_agents 中的 agent_id (release_id) 为 app_id，并带出版本策略信息：
        # release_policy / release_id（pinned 才有值）/ current_release_id / has_newer_release
        converted_sub_agents = []
        for sub_agent in config.sub_agents:
            sub_agent_copy = sub_agent.copy()
            try:
                state = describe_release_state(self.db, sub_agent)
                if state["app_id"]:
                    sub_agent_copy["agent_id"] = state["app_id"]
                sub_agent_copy["release_policy"] = state["release_policy"]
                sub_agent_copy["release_id"] = state["release_id"]
                sub_agent_copy["current_release_id"] = state["current_release_id"]
                sub_agent_copy["has_newer_release"] = state["has_newer_release"]
            except Exception as e:
                logger.warning(
                    f"转换 sub_agent agent_id 失败: {sub_agent.get('agent_id')}, 错误: {str(e)}"
                )
            converted_sub_agents.append(sub_agent_copy)

        # 构建返回的配置字典
        return {
            "id": config.id,
            "app_id": config.app_id,
            "default_model_config_id": config.default_model_config_id,
            "model_parameters": config.model_parameters,
            "orchestration_mode": config.orchestration_mode,
            "sub_agents": converted_sub_agents,
            "routing_rules": config.routing_rules,
            "execution_config": config.execution_config,
            # 主管即 Agent：回读必须带出，否则前端 getData 拿不到该键，
            # 缺省回填会把主管能力面重置为全默认（开关"保存后自动关闭"）
            "supervisor_config": config.supervisor_config,
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

    def check_config_data(self,app_id: uuid.UUID, data: MultiAgentConfigUpdate) -> MultiAgentConfig:
        # 1. 验证应用存在
        app = self.db.get(App, app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(app_id))

        # 2. 验证模型配置（如果提供了）
        if data.default_model_config_id:
            from app.repositories.tool_repository import ToolRepository

            tenant_id = ToolRepository.get_tenant_id_by_workspace_id(self.db, str(app.workspace_id))
            invoke_ref = ModelApiKeyService.resolve_invoke_ref(
                self.db,
                data.default_model_config_id,
                tenant_id=tenant_id,
            )
            if invoke_ref is None:
                ModelConfigService.raise_model_unavailable(
                    self.db,
                    data.default_model_config_id,
                    tenant_id=tenant_id,
                )

        # 3. 验证子 Agent 存在并获取发布版本 ID
        # agent_id 始终存子 Agent 的应用 ID（身份稳定）；版本由 release_policy / release_id 表达：
        # current=跟随最新，release_id 置空；pinned=落库校验过的 release_id。
        for sub_agent in data.sub_agents:
            agent_app_release = resolve_release_for_save(
                self.db,
                sub_agent.agent_id,
                sub_agent.release_policy,
                sub_agent.release_id,
                label=sub_agent.name,
            )
            if sub_agent.release_policy == "pinned":
                sub_agent.release_id = agent_app_release.id
            else:
                sub_agent.release_id = None

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

        # 处理 supervisor_config（主管即 Agent；None=裸主管现状，列保持 NULL）
        if getattr(data, "supervisor_config", None) is None:
            supervisor_config_data = None
        elif isinstance(data.supervisor_config, dict):
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config)
        else:
            supervisor_config_data = convert_uuids_to_str(data.supervisor_config.model_dump())

        # 处理 model_parameters（可能是 None、字典或 Pydantic 模型）
        if data.model_parameters is None:
            model_parameters_data = None
        # elif isinstance(data.model_parameters, dict):
        #     # 过滤掉值为 None 的字段
        #     model_parameters_data = {k: v for k, v in data.model_parameters.items() if v is not None}
        else:
            # 过滤掉值为 None 的字段
            # model_parameters_data = {k: v for k, v in data.model_parameters.model_dump().items() if v is not None}
            model_parameters_data = data.model_parameters

        config = MultiAgentConfig(
                app_id=app_id,
                master_agent_id=data.master_agent_id,
                master_agent_name=data.master_agent_name,
                default_model_config_id=data.default_model_config_id,
                model_parameters=model_parameters_data,
                orchestration_mode=data.orchestration_mode,
                sub_agents=sub_agents_data,
                # routing_rules=routing_rules_data,
                execution_config=execution_config_data,
                supervisor_config=supervisor_config_data,
                aggregation_strategy=data.aggregation_strategy
            )
        return config

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
        newConfig = self.check_config_data(app_id, data)
        if not config:
            config = newConfig
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

        # 完全替换配置，但对于数据库 NOT NULL 字段，如果新值是 None 则保留原值
        config.default_model_config_id = newConfig.default_model_config_id
        config.model_parameters = newConfig.model_parameters
        config.orchestration_mode = newConfig.orchestration_mode or config.orchestration_mode
        config.sub_agents = newConfig.sub_agents if newConfig.sub_agents is not None else config.sub_agents
        config.routing_rules = newConfig.routing_rules
        config.execution_config = newConfig.execution_config if newConfig.execution_config else config.execution_config
        # 主管即 Agent：supervisor_config 列 nullable，None=不更新/清空为裸主管语义一致，
        # 直接赋值（与 execution_config 的"非空才替换"不同：能力面显式置空即用户意图）
        config.supervisor_config = newConfig.supervisor_config
        config.aggregation_strategy = newConfig.aggregation_strategy or config.aggregation_strategy
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
        request: MultiAgentRunRequest,
        source: str = HitLogSource.CONSOLE,
    ) -> dict:
        """运行多 Agent 任务

        Args:
            app_id: 应用 ID
            request: 运行请求
            source: 标注命中来源（console=控制台试运行，external=外部/分享入口）

        Returns:
            执行结果
        """
        # 1. 获取配置（draft/run 入口用 AsyncSession，get_config 是同步 scalars 会拿到协程；
        #    用 get_config_async 兼容两种 session——AsyncSession 走 execute，sync 走 get_config）
        config = await self.get_config_async(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))

        if not config.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.RESOURCE_DISABLED)

        # S1 轮次锚点：预生成 assistant 消息 ID，下传给编排器写 master 执行记录，
        # 落库用同一 ID，日志详情按消息挂载节点不依赖时序吸附。
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

        # 标注命中：命中则不进编排器，直接落库返回标注答案（与正式对话/Agent 试运行一致）
        _annotation_hit = await self._cluster_annotation_hit(
            config, request.message, request.conversation_id, message_id, user_message_id,
            source=source,
        )
        if _annotation_hit is not None:
            return {
                "conversation_id": str(request.conversation_id) if request.conversation_id else None,
                "message": _annotation_hit["answer"],
                "message_id": str(message_id),
                "user_message_id": str(user_message_id),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        # 2. 创建编排器
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        # 新会话开场白（features.opening_statement 开启才查库；老集群 features 为空，零开销）
        opening_statement, _opening_suggested_questions = await self._resolve_cluster_opening(
            orchestrator, request.conversation_id, request.variables
        )

        # 3. 执行任务
        result = await orchestrator.execute(
            message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            variables=request.variables,
            use_llm_routing=getattr(request, 'use_llm_routing', True),  # 默认启用 LLM 路由
            web_search=getattr(request, 'web_search', False),  # 网络搜索参数
            memory=getattr(request, 'memory', True),  # 记忆功能参数
            message_id=message_id,
            thinking=getattr(request, 'thinking', False),  # 深度思考（缺省 False = 旧行为）
            files=getattr(request, 'files', None) or None,  # 附件（缺省空 = 旧行为）
            user_message_id=user_message_id,  # 情绪感知缓存键（features.emotion_reply 开启才用）
            opening_statement=opening_statement,  # 开场白注入主管历史（缺省 None = 旧行为）
        )

        # S3 落库收口：非流式同样经 BatchPersistQueue 统一落库（与流式同一口径），
        # _save_conversation_message 直存废弃。外层调用方（shared 非流式 deprecated
        # 入口等）不再各自 add_message —— 同轮两条 assistant 的根因。
        from app.services.batch_persist_queue import BatchPersistQueue, PersistTask
        from app.services.chat_context import StreamResult as _StreamResult
        _result = _StreamResult()
        _result.message_id = message_id
        _result.user_message_id = user_message_id
        _result.full_content = result.get("message", "")
        _result.total_tokens = (result.get("usage") or {}).get("total_tokens", 0)
        _result.assistant_meta = {
            "mode": result.get("mode"),
            "elapsed_time": result.get("elapsed_time"),
            "merge_mode_actual": getattr(orchestrator, "_merge_mode_actual", None),
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            })
        }
        # 建议问题 / 引用 / 语音落库（与 Agent 应用一致）；对应 features 未开启时 result 无这些键，不写任何字段
        if "suggested_questions" in result:
            _result.suggested_questions = result.get("suggested_questions") or []
        if "citations" in result:
            _result.citations = result.get("citations") or []
        if result.get("audio_url"):
            _result.audio_url = result.get("audio_url")
            _result.audio_status = result.get("audio_status")
        if request.conversation_id is not None:
            _persist_args = {
                "conversation_id": str(request.conversation_id),
                "result": _result,
                "user_message_id_override": user_message_id,
                "user_message_content": request.message,
                # 本轮变量值随 user 消息落库，重新生成时回读（无变量不加键）
                "variables_meta": request.variables or None,
                # user 消息接在用户当前所处的 assistant 版本之后，使切换版本时按 parent 隔离后续对话
                "user_parent_message_id": await self._get_last_current_assistant_id(request.conversation_id),
                "should_memorize": orchestrator.cluster_memory_enabled(),
            }
            # 附件落库（与 Agent / 分享端集群同口径）：缺了 meta.files，重新生成时
            # _restore_files_from_parent 拿不到 FileInput，表现为"首轮能解析附件、重新生成取不到"。
            if getattr(request, "files", None):
                _files_meta, _history_files = await self._build_cluster_files_persist(
                    request.files, orchestrator
                )
                _persist_args["files_meta"] = _files_meta
                if _history_files:
                    _result.history_files = _history_files
            # 新会话开场白落库（与 Agent 一致）：首轮 user 挂在开场白下，切版本分支链才带得出开场白
            if opening_statement:
                _persist_args["is_new_conversation"] = True
                _persist_args["opening_statement"] = opening_statement
                _persist_args["opening_message_id"] = uuid.uuid4()
                _persist_args["opening_suggested_questions"] = _opening_suggested_questions
            await BatchPersistQueue.enqueue(PersistTask(
                task_type="save_messages",
                args=_persist_args,
            ))

        # 上下文引擎 after_turn（仅本轮实际走了 features.context_engine 才入队；老集群恒为 None）
        _ctx_after_turn = orchestrator.context_engine_after_turn_args(request.conversation_id)
        if _ctx_after_turn:
            try:
                await BatchPersistQueue.enqueue(PersistTask(task_type="after_turn", args=_ctx_after_turn))
            except Exception as e:
                logger.warning("入队 context_engine after_turn 失败（不影响对话）", extra={"error": str(e)})

        # S1 轮次锚点：消息落库后回填 master 执行记录的 message_id（幂等）。
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                from app.services.batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(message_id),
                    },
                ))
            except Exception as e:
                logger.warning("回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)})

        return result

    async def run_stream(
        self,
        app_id: uuid.UUID,
        request: MultiAgentRunRequest,
        storage_type :str,
        user_rag_memory_id :str,
        source: str = HitLogSource.CONSOLE,
    ):
        """运行多 Agent 任务（流式返回）

        Args:
            app_id: 应用 ID
            request: 运行请求
            source: 标注命中来源（console=控制台试运行，external=外部/分享入口）

        Yields:
            SSE 格式的事件流
        """
        # 1. 获取配置
        config = await self.get_config_async(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))

        if not config.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.NOT_FOUND)

        full_content = ""
        total_tokens = 0
        # 本轮 assistant 消息 ID（S1 轮次锚点）：预先生成并下传给编排器，
        # master 执行记录据此带 message_id（经 BatchPersistQueue 回填），
        # 日志详情按消息挂载节点时不再依赖"时序就近"兜底——
        # 兜底在多轮/子 Agent 并发场景下会把同一轮的节点吸附到别的消息，
        # 表现为"一轮会话分散到多条日志消息"。
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

        # 标注命中：命中则不进编排器，直接落库并下发 message/end（与正式对话一致）
        _annotation_hit = await self._cluster_annotation_hit(
            config, request.message, request.conversation_id, message_id, user_message_id,
            storage_type=storage_type, user_rag_memory_id=user_rag_memory_id, source=source,
        )
        if _annotation_hit is not None:
            _answer = _annotation_hit["answer"]
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(request.conversation_id) if request.conversation_id else None, 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"
            yield f"event: message\ndata: {json.dumps({'content': _answer, 'conversation_id': str(request.conversation_id) if request.conversation_id else None}, ensure_ascii=False)}\n\n"
            yield f"event: end\ndata: {json.dumps({'conversation_id': str(request.conversation_id) if request.conversation_id else None, 'message_id': str(message_id), 'message_length': len(_answer), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}, ensure_ascii=False)}\n\n"
            return

        # 2. 创建编排器
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        # 新会话开场白（features.opening_statement 开启才查库；老集群 features 为空，零开销）
        opening_statement, _opening_suggested_questions = await self._resolve_cluster_opening(
            orchestrator, request.conversation_id, request.variables
        )

        # 3. 流式执行任务
        # S4：不再拦截 sub_usage 字符串累加 token —— orchestrator 内建
        # per-turn 账本（routing/sub/merge 分层），在 end 事件统一发布
        # usage（唯一权威口径）。本层只透传事件 + 累计 message 正文。
        total_tokens = 0
        # end 事件携带的对话能力附加字段（建议问题/引用/语音）；features 未开启时为空
        end_extras: dict = {}
        async for event in orchestrator.execute_stream(
            message=request.message,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            variables=request.variables,
            use_llm_routing=getattr(request, 'use_llm_routing', True),
            web_search=getattr(request, 'web_search', False),  # 网络搜索参数
            memory=getattr(request, 'memory', True) , # 记忆功能参数
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            message_id=message_id,
            thinking=getattr(request, 'thinking', False),  # 深度思考（缺省 False = 旧行为）
            files=getattr(request, 'files', None) or None,  # 附件（缺省空 = 旧行为）
            user_message_id=user_message_id,  # 情绪感知缓存键（features.emotion_reply 开启才用）
            opening_statement=opening_statement,  # 开场白注入主管历史（缺省 None = 旧行为）
        ):
            _event_name = ""
            if event.startswith("event:"):
                _event_name = event[6:].split("\n", 1)[0].strip()

            if _event_name == "end" and "data:" in event:
                # S4：从 end 事件读取本轮 usage（账本口径），作为落库 meta 的
                # 唯一 token 来源。
                try:
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    total_tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
                    # 对话能力附加字段（仅对应 features 开启时 end 才带这些键）
                    for _k in ("suggested_questions", "citations", "audio_url", "audio_status"):
                        if _k in data:
                            end_extras[_k] = data[_k]
                except Exception:
                    pass

            yield event
            # 落库正文只认集群级的 `message` 事件（按事件名判定）。
            # 子 Agent 的正文走 `sub_agent_message`：若一并累加，落库的 assistant
            # 正文会比界面显示多出一份重复内容（刷新后主气泡变长）。
            if _event_name == "message" and "data:" in event:
                try:
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    if "content" in data:
                        full_content += data["content"]
                except Exception:
                    pass

        # S3 落库收口：本服务（试运行/API 场景）作为多 Agent 流的唯一落库层，
        # 改经 BatchPersistQueue 统一写入；_save_conversation_message 直存废弃
        # （同轮两条 assistant 的根因之一：外层 app_chat_service/shared 层若也
        # 各存一份）。落库正文 = orchestrator message 事件序列（与前端主气泡
        # 展示同口径），不再是各层自己拼接的中间产物。
        import time as _time
        _master_started_at = getattr(orchestrator, "_master_started_at", None)
        elapsed_time = (_time.time() - _master_started_at) if _master_started_at else 0.0
        from app.services.batch_persist_queue import BatchPersistQueue, PersistTask
        from app.services.chat_context import StreamResult as _StreamResult
        _result = _StreamResult()
        _result.message_id = message_id
        _result.user_message_id = user_message_id
        _result.full_content = full_content
        _result.total_tokens = total_tokens
        _result.assistant_meta = {
            "elapsed_time": elapsed_time,
            "mode": getattr(orchestrator, "_normalized_mode", None),
            "merge_mode_actual": getattr(orchestrator, "_merge_mode_actual", None),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
        }
        # 建议问题 / 引用 / 语音落库（与 Agent 应用一致）；对应 features 未开启时 end_extras 为空，不写任何字段
        if end_extras:
            if "suggested_questions" in end_extras:
                _result.suggested_questions = end_extras.get("suggested_questions") or []
            if "citations" in end_extras:
                _result.citations = end_extras.get("citations") or []
            if end_extras.get("audio_url"):
                _result.audio_url = end_extras.get("audio_url")
                _result.audio_status = end_extras.get("audio_status")
        # 附件落库（与 Agent / 分享端集群同口径）：缺了 meta.files，重新生成时
        # _restore_files_from_parent 拿不到 FileInput，表现为"首轮能解析附件、重新生成取不到"。
        _files_meta: list = []
        if getattr(request, "files", None):
            _files_meta, _history_files = await self._build_cluster_files_persist(request.files, orchestrator)
            if _history_files:
                _result.history_files = _history_files
        await BatchPersistQueue.enqueue(PersistTask(
            task_type="save_messages",
            args={
                "conversation_id": str(request.conversation_id),
                "result": _result,
                "user_message_id_override": user_message_id,
                "user_message_content": request.message,
                # 本轮变量值随user 消息落库，重新生成时回读（无变量不加键）
                "variables_meta": request.variables or None,
                # 本轮附件引用（重新生成时据此还原 FileInput）
                "files_meta": _files_meta,

                # user 消息接在用户当前所处的 assistant 版本之后，使切换版本时按 parent 隔离后续对话
                "user_parent_message_id": await self._get_last_current_assistant_id(request.conversation_id),
                "should_memorize": orchestrator.cluster_memory_enabled(),
                # 新会话开场白随本轮落库（与 Agent 一致），切版本分支链才能带出开场白；无开场白不加键
                **(
                    {
                        "is_new_conversation": True,
                        "opening_statement": opening_statement,
                        "opening_message_id": uuid.uuid4(),
                        "opening_suggested_questions": _opening_suggested_questions,
                    }
                    if opening_statement else {}
                ),
            },
        ))

        # context_engine 轮后摘要（仅本轮实际走了上下文引擎才入队；老集群恒为 None）
        _ctx_after_args = orchestrator.context_engine_after_turn_args(request.conversation_id)
        if _ctx_after_args:
            await BatchPersistQueue.enqueue(PersistTask(task_type="after_turn", args=_ctx_after_args))

        # S1 轮次锚点：消息落库后把 master 执行记录关联到本轮 assistant 消息
        #（执行记录在流式开始时创建，当时消息尚未落库写 message_id 会 FK 违例，
        # 因此在此处回填；幂等：只更新仍为空的记录）。
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                from app.services.batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(message_id),
                    },
                ))
            except Exception as e:
                logger.warning("回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)})

    # ==================== 重新生成功能（对齐 Agent 应用多版本语义） ====================

    def _assert_regenerate_quota(self, message_id: uuid.UUID) -> None:
        """校验本轮版本数是否已达上限，超限抛 BusinessException（对齐 WorkflowService 口径）。

        计数同 parent_message_id 下未删除的 assistant 版本（含原始回复）。
        历史数据中 original_msg 可能未挂 parent_message_id（未计入查询），需补计 1。
        """
        from app.models import Message
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        parent_msg = self._locate_parent_user_message(original_msg)
        existing = self.db.query(Message).filter(
            Message.parent_message_id == parent_msg.id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
        ).count()
        if not original_msg.parent_message_id:
            existing += 1
        if existing >= self.MAX_REGENERATE_VERSIONS:
            raise BusinessException(
                f"该回复已达最大版本数（{self.MAX_REGENERATE_VERSIONS} 个），无法继续重新生成",
                BizCode.VERSION_LIMIT_EXCEEDED,
            )

    def _locate_parent_user_message(self, original_msg):
        """定位原 assistant 消息对应的父 user 消息（对齐 WorkflowService._locate_parent_user_message）。

        查找顺序：
        1. 优先用 parent_message_id；为空/指向非 user 时回退到时间回溯；
        2. 回溯同会话 created_at 早于或等于原消息的最近一条 user 消息（不按 is_deleted
           过滤——即使被删仍是本轮实际提问；用 <= 兼容 user/assistant 同毫秒入库）；
        3. 仍找不到再放宽到本会话内最近一条 user 消息（兜底，覆盖 created_at 异常脏数据）。

        若定位到的父消息已被逻辑删除（重新生成前误删了原提问），自动恢复后继续，
        而非直接报错——避免误删提问导致该轮回复彻底无法重新生成。
        """
        from app.models import Message

        parent_msg = None
        if original_msg.parent_message_id:
            candidate = self.db.get(Message, original_msg.parent_message_id)
            if candidate and candidate.role == "user":
                parent_msg = candidate
        if not parent_msg:
            parent_msg = (
                self.db.query(Message)
                .filter(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at <= original_msg.created_at,
                )
                .order_by(Message.created_at.desc())
                .first()
            )
        if not parent_msg:
            # 兜底：同会话内最近一条 user 消息（不论时间顺序），覆盖 created_at 异常的脏数据
            parent_msg = (
                self.db.query(Message)
                .filter(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
                .first()
            )
        if not parent_msg:
            raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
        # 回填 parent_message_id 便于后续版本化关联；父消息被删则自动恢复
        if original_msg.parent_message_id != parent_msg.id:
            original_msg.parent_message_id = parent_msg.id
        if parent_msg.is_deleted:
            parent_msg.is_deleted = False
        self.db.commit()
        return parent_msg

    def _next_regenerate_version(self, original_msg, parent_msg_id: uuid.UUID) -> int:
        """计算新版本号：同 parent_message_id 下未删除 assistant 版本的最大 version + 1。

        多分支场景下用户可能切回旧版本再重新生成，若用 original.version+1 会与已存在
        的更新版本冲突，导致版本切换混乱，故取同组最大 version + 1。
        """
        from app.models import Message
        base = original_msg.version or 1
        siblings = self.db.query(Message).filter(
            Message.parent_message_id == parent_msg_id,
            Message.conversation_id == original_msg.conversation_id,
            Message.role == "assistant",
            Message.is_deleted.is_not(True),
        ).all()
        for s in siblings:
            if s.version and s.version > base:
                base = s.version
        return base + 1

    def _mark_siblings_not_current(self, conversation_id, parent_msg_id, exclude_id=None) -> None:
        """把同一轮（同 parent_message_id）下所有 assistant 版本置为非当前。

        注意：不能依赖 ``update().where(is_current.is_(True))`` 之类的条件去判断"是否要置"，
        直接按行集合统一置 False；幂等——重复置 False 无副作用。
        """
        from app.models import Message
        q = self.db.query(Message).filter(
            Message.conversation_id == conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        )
        if exclude_id is not None:
            q = q.filter(Message.id != exclude_id)
        q.update({"is_current": False}, synchronize_session=False)
        self.db.commit()

    def _mark_message_current(self, message_id: uuid.UUID, is_current: bool) -> None:
        """按主键把单条消息置为当前/非当前（回滚用，不依赖 ORM 实例状态）。"""
        from app.models import Message
        self.db.query(Message).filter(Message.id == message_id).update(
            {"is_current": is_current}, synchronize_session=False
        )
        self.db.commit()

    def _restore_files_from_parent(self, parent_msg):
        """从父 user 消息 meta_data.files 还原 FileInput 列表（与 Agent regenerate 同口径）。"""
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                from app.services.multimodal_service import deserialize_file_reference
                files = []
                for f in meta_files:
                    try:
                        files.append(deserialize_file_reference(f))
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")
                        continue
        return files

    @staticmethod
    def _restore_variables_from_parent(parent_msg, override: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """还原首轮对话传入的变量值：父 user 消息 meta_data.variables 为底，请求显式传入的覆盖其上。

        重新生成沿用原提问的变量，用户不必重传；请求里传了的变量以请求为准（与工作流重新生成的合并语义一致）。
        存量会话没有落库变量时返回 override 原样（可能为 None）。
        """
        saved = None
        if parent_msg is not None and parent_msg.meta_data:
            saved = parent_msg.meta_data.get("variables")
        if not isinstance(saved, dict) or not saved:
            return override
        merged = dict(saved)
        if override:
            merged.update(override)
        return merged

    async def _resolve_regenerate_config(self, app_id: uuid.UUID, config: Optional[MultiAgentConfig]):
        """解析重跑用的集群配置：config 传入（分享端发布快照）则直接用；否则取草稿。

        控制台试运行走草稿（与集群 run 同源）；分享端走发布快照。任一缺失都抛错。
        """
        if config is not None:
            return config
        resolved = await self.get_config_async(app_id)
        if not resolved:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))
        if not resolved.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.RESOURCE_DISABLED)
        return resolved

    def _build_regenerate_meta(self, original_message_id, usage=None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """新版本 assistant 消息的 meta_data：usage + 能力附加字段 + regenerated_from 溯源。"""
        meta: Dict[str, Any] = {
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "regenerated_from": str(original_message_id),
        }
        for k in ("suggested_questions", "citations", "audio_url", "audio_status", "reasoning_content"):
            if extra and extra.get(k):
                meta[k] = extra[k]
        return meta

    def _save_regenerated_message(
        self,
        *,
        conversation_id: uuid.UUID,
        content: str,
        version: int,
        parent_message_id: uuid.UUID,
        meta_data: Dict[str, Any],
        message_id: Optional[uuid.UUID] = None,
    ):
        """落库新版本 assistant 消息（同步直插，对齐 WorkflowService.regenerate 行 5747 模式）。

        响应需立即引用新 message_id、版本切换要即时可用，故不走 BatchPersistQueue
        异步队列。同时把会话 message_count +1（直插场景队列不会代计）。
        """
        from app.models import Conversation, Message
        new_msg = Message(
            id=message_id or uuid.uuid4(),
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            version=version,
            is_current=True,
            parent_message_id=parent_message_id,
            meta_data=meta_data,
            status="completed",
        )
        self.db.add(new_msg)
        conv = self.db.get(Conversation, conversation_id)
        if conv:
            conv.message_count = int(conv.message_count or 0) + 1
        self.db.commit()
        self.db.refresh(new_msg)
        return new_msg

    async def regenerate(
        self,
        *,
        message_id: uuid.UUID,
        app_id: uuid.UUID,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        thinking: bool = False,
        config: Optional[MultiAgentConfig] = None,
        source: str = HitLogSource.CONSOLE,
    ) -> Dict[str, Any]:
        """重新生成集群回复（非流式，多版本支持）。

        对齐 Agent 应用的重新生成语义，但执行引擎是多 Agent 编排器（保留主管路由/
        子 Agent 编排），而非单 Agent。版本管理骨架与 WorkflowService.regenerate 一致：
        定位父 user 消息 → 旧版本置非当前 → 取同组 max(version)+1 → 基于"父消息之前"
        的历史重跑（编排器 regenerate_before 截断）→ 落库新版本。

        Args:
            message_id: 原 assistant 消息 ID
            app_id: 集群应用 ID（取配置/观测用）
            config: 可选。传入（分享端发布快照）直接用；缺省取当前草稿（控制台试运行）。
        """
        from app.models import Message

        # 1. 校验原消息
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. 版本配额（开跑前拒绝，避免编排后才发现超限）
        self._assert_regenerate_quota(message_id)

        # 3. 定位父 user 消息（误删则自动恢复）
        parent_msg = self._locate_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id
        conversation_id = original_msg.conversation_id
        user_message_content = parent_msg.content or ""

        # 4. 版本号：同组 max+1；旧版本（含原消息及其兄弟）统一置非当前
        new_version = self._next_regenerate_version(original_msg, parent_msg_id)
        self._mark_siblings_not_current(conversation_id, parent_msg_id)

        # 5. 提取父消息附件（与 Agent regenerate 同口径）
        files = self._restore_files_from_parent(parent_msg)
        # 沿用首轮对话的变量值（请求显式传入的覆盖其上），用户不必重传必填变量
        variables = self._restore_variables_from_parent(parent_msg, variables)

        # 6. 解析配置并重建编排器；注入历史截断时间（= 父消息创建时间）
        resolved_config = await self._resolve_regenerate_config(app_id, config)

        new_message_id = uuid.uuid4()

        # 标注命中：重新生成同样先查标注，命中则不进编排器，直接把标注答案作为新版本落库
        #（仅匹配，不重写 user 消息；版本化落库走 _save_regenerated_message，与正常重跑同口径）
        _annotation_hit = await self._match_cluster_annotation(
            resolved_config.app_id, user_message_content, source=source
        )
        if _annotation_hit is not None:
            new_msg = self._save_regenerated_message(
                conversation_id=conversation_id,
                content=_annotation_hit["answer"],
                version=new_version,
                parent_message_id=parent_msg_id,
                meta_data=self._build_regenerate_meta(
                    message_id,
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    extra={"annotation_hit": {
                        "annotation_id": _annotation_hit["annotation_id"],
                        "similarity": _annotation_hit["similarity"],
                        "question": _annotation_hit["question"],
                    }},
                ),
                message_id=new_message_id,
            )
            logger.info(
                "集群重新生成命中标注",
                extra={
                    "original_message_id": str(message_id),
                    "new_message_id": str(new_msg.id),
                    "version": new_version,
                    "conversation_id": str(conversation_id),
                },
            )
            return {
                "message_id": str(new_msg.id),
                "message": _annotation_hit["answer"],
                "reasoning_content": None,
                "version": new_version,
                "conversation_id": str(conversation_id),
                "suggested_questions": [],
                "citations": [],
                "audio_url": None,
                "audio_status": None,
            }

        orchestrator = await MultiAgentOrchestrator.create(self.db, resolved_config)

        try:
            result = await orchestrator.execute(
                message=user_message_content,
                conversation_id=conversation_id,
                user_id=user_id,
                variables=variables,
                use_llm_routing=True,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                message_id=new_message_id,
                thinking=thinking,
                files=files or None,
                user_message_id=parent_msg_id,  # 情绪感知缓存键；不新建 user 行
                opening_statement=None,  # 重新生成不重复开场白
                regenerate_before=parent_msg.created_at,  # 历史截断到本轮提问之前
            )
        except Exception:
            # 编排失败：把原版本恢复为当前，避免出现"无任何当前回复"的空窗
            self._mark_message_current(original_msg.id, True)
            raise

        # 7. 落库新版本 assistant 消息
        usage = result.get("usage") or {}
        new_msg = self._save_regenerated_message(
            conversation_id=conversation_id,
            content=result.get("message", ""),
            version=new_version,
            parent_message_id=parent_msg_id,
            meta_data=self._build_regenerate_meta(message_id, usage=usage, extra=result),
            message_id=new_message_id,
        )

        # 8. 主执行记录关联到新消息（观测挂载；失败不阻断）
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                from app.services.batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(new_message_id),
                    },
                ))
            except Exception as e:
                logger.warning("回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)})

        logger.info(
            "集群重新生成回复成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_msg.id),
                "version": new_version,
                "conversation_id": str(conversation_id),
            },
        )

        return {
            "message_id": str(new_msg.id),
            "message": result.get("message", ""),
            "reasoning_content": result.get("reasoning_content"),
            "version": new_version,
            "conversation_id": str(conversation_id),
            "suggested_questions": result.get("suggested_questions", []),
            "citations": result.get("citations", []),
            "audio_url": result.get("audio_url"),
            "audio_status": result.get("audio_status"),
        }

    async def regenerate_stream(
        self,
        *,
        message_id: uuid.UUID,
        app_id: uuid.UUID,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        thinking: bool = False,
        config: Optional[MultiAgentConfig] = None,
        source: str = HitLogSource.CONSOLE,
    ) -> AsyncGenerator[str, None]:
        """重新生成集群回复（流式输出，多版本支持）。

        SSE 契约对齐 WorkflowService.regenerate_stream：
        start{message_id,user_message_id,version,conversation_id} → 编排器内容事件
        （message/reasoning/sub_agent_* 等）→ end → regenerate_end。
        落库与版本管理骨架同 regenerate（非流式）。
        """
        import asyncio as _asyncio
        import json as _json
        import time as _time
        from app.models import Message

        def _sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"

        # 1-4. 校验 + 配额 + 父消息 + 版本号 + 置旧版本非当前（与非流式同一骨架；
        #       必须在首个 SSE 事件前完成——开流后无法回传干净的 HTTP 错误）
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)
        self._assert_regenerate_quota(message_id)
        parent_msg = self._locate_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id
        conversation_id = original_msg.conversation_id
        user_message_content = parent_msg.content or ""
        new_version = self._next_regenerate_version(original_msg, parent_msg_id)
        self._mark_siblings_not_current(conversation_id, parent_msg_id)
        files = self._restore_files_from_parent(parent_msg)
        # 沿用首轮对话的变量值（请求显式传入的覆盖其上），用户不必重传必填变量
        variables = self._restore_variables_from_parent(parent_msg, variables)
        resolved_config = await self._resolve_regenerate_config(app_id, config)

        new_message_id = uuid.uuid4()
        start_time = _time.time()

        # start 事件：带新消息 id / 版本号（前端据此挂载本轮气泡与版本指示）
        yield _sse("start", {
            "conversation_id": str(conversation_id),
            "message_id": str(new_message_id),
            "user_message_id": str(parent_msg_id),
            "version": new_version,
            "timestamp": start_time,
        })

        # 标注命中：重新生成同样先查标注，命中则不进编排器，直接把标注答案作为新版本落库，
        # 下发 message/end/regenerate_end（与正常重跑同一事件契约）。
        _annotation_hit = await self._match_cluster_annotation(
            resolved_config.app_id, user_message_content, source=source
        )
        if _annotation_hit is not None:
            _answer = _annotation_hit["answer"]
            self._save_regenerated_message(
                conversation_id=conversation_id,
                content=_answer,
                version=new_version,
                parent_message_id=parent_msg_id,
                meta_data=self._build_regenerate_meta(
                    message_id,
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    extra={"annotation_hit": {
                        "annotation_id": _annotation_hit["annotation_id"],
                        "similarity": _annotation_hit["similarity"],
                        "question": _annotation_hit["question"],
                    }},
                ),
                message_id=new_message_id,
            )
            yield _sse("message", {"content": _answer})
            yield _sse("end", {
                "status": "completed",
                "elapsed_time": _time.time() - start_time,
                "conversation_id": str(conversation_id),
                "message_id": str(new_message_id),
                "message_length": len(_answer),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
            yield _sse("regenerate_end", {
                "message_id": str(new_message_id),
                "version": new_version,
                "conversation_id": str(conversation_id),
            })
            return

        orchestrator = await MultiAgentOrchestrator.create(self.db, resolved_config)

        full_content = ""
        usage: Dict[str, Any] = {}
        end_extras: Dict[str, Any] = {}
        stream_failed = False
        error_emitted = False
        failure_error: Optional[str] = None
        try:
            async for event in orchestrator.execute_stream(
                message=user_message_content,
                conversation_id=conversation_id,
                user_id=user_id,
                variables=variables,
                use_llm_routing=True,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                message_id=new_message_id,
                thinking=thinking,
                files=files or None,
                user_message_id=parent_msg_id,
                opening_statement=None,
                regenerate_before=parent_msg.created_at,
            ):
                # 解析事件名以累积正文 / 提取 end 的 usage 与能力附加字段
                _ev = ""
                if event.startswith("event:"):
                    _ev = event[6:].split("\n", 1)[0].strip()
                if "data:" in event:
                    try:
                        _data = _json.loads(event.split("data: ", 1)[1].strip())
                    except Exception:
                        _data = {}
                else:
                    _data = {}

                # 编排器自身的 start/end 透传会造成前端重复 start；这里接管收尾，
                # 只转发内容/过程事件，start/end 由本方法统一发。
                if _ev == "start":
                    continue
                if _ev == "end":
                    usage = _data.get("usage") or {}
                    for _k in ("suggested_questions", "citations", "audio_url", "audio_status"):
                        if _k in _data:
                            end_extras[_k] = _data[_k]
                    continue  # 末尾统一补标准 end + regenerate_end
                if _ev == "error":
                    # 编排器 execute_stream 的失败语义是发 error 事件（吞异常正常返回），
                    # 不抛异常——必须在这里识别并走回滚，否则空/部分正文会被落成新版本。
                    stream_failed = True
                    failure_error = _data.get("error") or "集群重新生成失败"
                    error_emitted = True  # 编排器的 error 帧已原样透传，收尾不再重复发
                    yield event
                    break

                if _ev == "message" and "content" in _data:
                    full_content += _data["content"]
                yield event
        except (GeneratorExit, _asyncio.CancelledError):
            # 消费方中途断开（GeneratorExit / 任务取消均属 BaseException，不会被下方
            # except Exception 捕获）：此刻旧版本已被置为非当前、新版本尚未落库，
            # 不回滚会让该轮所有版本都 is_current=false。这里不能再 yield，只做 DB 收尾后重抛。
            try:
                self._mark_message_current(original_msg.id, True)
            except Exception as rollback_err:
                logger.warning("断开后回滚原版本失败", extra={"error": str(rollback_err)})
            raise
        except Exception as e:
            stream_failed = True
            failure_error = str(e)

        # 失败回滚：恢复原版本为当前，不落新版本。
        # 失败帧用 error 事件（与 Agent / 编排器失败语义一致），不再发 status=failed 的 end；
        # 编排器已透传过 error 帧时不重复发。
        if stream_failed:
            self._mark_message_current(original_msg.id, True)
            if not error_emitted:
                yield _sse("error", {
                    "conversation_id": str(conversation_id),
                    "error": failure_error or "集群重新生成失败",
                    "elapsed_time": _time.time() - start_time,
                    "timestamp": _time.time(),
                })
            return

        # 7. 落库新版本 assistant 消息
        new_msg = self._save_regenerated_message(
            conversation_id=conversation_id,
            content=full_content,
            version=new_version,
            parent_message_id=parent_msg_id,
            meta_data=self._build_regenerate_meta(message_id, usage=usage, extra=end_extras),
            message_id=new_message_id,
        )

        # 8. 主执行记录关联（观测挂载；失败不阻断）
        master_execution_id = getattr(orchestrator, "current_execution_id", None)
        if master_execution_id is not None:
            try:
                from app.services.batch_persist_queue import BatchPersistQueue, PersistTask
                await BatchPersistQueue.enqueue(PersistTask(
                    task_type="link_agent_execution_message",
                    args={
                        "execution_id": str(master_execution_id),
                        "message_id": str(new_message_id),
                    },
                ))
            except Exception as e:
                logger.warning("回填主执行记录 message_id 失败（不影响对话）", extra={"error": str(e)})

        logger.info(
            "集群重新生成回复成功（流式）",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_msg.id),
                "version": new_version,
                "conversation_id": str(conversation_id),
            },
        )

        # 9. 标准 end + regenerate_end（与 workflow 重新生成契约一致）
        yield _sse("end", {
            "status": "completed",
            "elapsed_time": _time.time() - start_time,
            "conversation_id": str(conversation_id),
            "message_id": str(new_message_id),
            "message_length": len(full_content),
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            **end_extras,
        })
        yield _sse("regenerate_end", {
            "message_id": str(new_message_id),
            "version": new_version,
            "conversation_id": str(conversation_id),
        })

    async def _save_conversation_message(
        self,
        conversation_id: uuid.UUID,
        user_message: str,
        assistant_message: str,
        meta_data: dict,
        app_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        message_id: Optional[uuid.UUID] = None,
        user_message_id: Optional[uuid.UUID] = None
    ) -> None:
        """保存会话消息

        Args:
            conversation_id: 会话ID
            user_message: 用户消息
            assistant_message: AI 回复消息
            meta_data: 元数据（包括 token 消耗）
            app_id: 应用ID
            user_id: 用户ID
            message_id: assistant 消息 ID（轮次锚点，落库与执行记录关联用同一 ID）
            user_message_id: user 消息 ID（轮次锚点）
        """
        try:
            from app.services.conversation_service import ConversationService
            from app.models import Conversation, Message

            if isinstance(self.db, AsyncSession):
                async with get_async_db_context() as db:
                    conversation = await db.get(Conversation, conversation_id)
                    if not conversation:
                        raise ResourceNotFoundException("会话", str(conversation_id))

                    for role, content, message_meta, msg_id in (
                        ("user", user_message, None, user_message_id),
                        ("assistant", assistant_message, meta_data, message_id),
                    ):
                        message = Message(
                            id=msg_id or uuid.uuid4(),
                            conversation_id=conversation_id,
                            role=role,
                            content=content,
                            meta_data=message_meta,
                            status="completed",
                        )
                        db.add(message)
                        conversation.message_count = (conversation.message_count or 0) + 1
                        if conversation.message_count <= 2 and role == "user":
                            conversation.title = content[:50] + ("..." if len(content) > 50 else "")

                    await db.commit()
            else:
                conversation_service = ConversationService(self.db)
                conversation_service.add_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=user_message,
                    message_id=user_message_id,
                )
                conversation_service.add_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_message,
                    meta_data=meta_data,
                    message_id=message_id,
                )

            logger.debug(
                "保存多 Agent 会话消息",
                extra={
                    "conversation_id": conversation_id,
                    "user_message_length": len(user_message),
                    "assistant_message_length": len(assistant_message)
                }
            )

        except Exception as e:
            logger.warning("保存会话消息失败", extra={"error": str(e)})

    # def add_sub_agent(
    #     self,
    #     app_id: uuid.UUID,
    #     agent_id: uuid.UUID,
    #     name: str,
    #     role: Optional[str] = None,
    #     priority: int = 1,
    #     capabilities: Optional[List[str]] = None
    # ) -> MultiAgentConfig:
    #     """添加子 Agent

    #     Args:
    #         app_id: 应用 ID
    #         agent_id: Agent ID
    #         name: Agent 名称
    #         role: 角色描述
    #         priority: 优先级
    #         capabilities: 能力列表

    #     Returns:
    #         更新后的配置
    #     """
    #     config = self.get_config(app_id)
    #     if not config:
    #         raise ResourceNotFoundException("多 Agent 配置", str(app_id))

    #     # 验证 Agent 存在
    #     agent = self.db.get(AgentConfig, agent_id)
    #     if not agent:
    #         raise ResourceNotFoundException("Agent", str(agent_id))

    #     # 检查是否已存在
    #     for sub_agent in config.sub_agents:
    #         if sub_agent["agent_id"] == str(agent_id):
    #             raise BusinessException("Agent 已存在于配置中", BizCode.DUPLICATE_RESOURCE)

    #     # 添加子 Agent
    #     new_sub_agent = {
    #         "agent_id": str(agent_id),
    #         "name": name,
    #         "role": role,
    #         "priority": priority,
    #         "capabilities": capabilities or []
    #     }

    #     config.sub_agents.append(new_sub_agent)

    #     # 标记为已修改
    #     self.db.add(config)
    #     self.db.commit()
    #     self.db.refresh(config)

    #     logger.info(
    #         "添加子 Agent 成功",
    #         extra={
    #             "config_id": str(config.id),
    #             "agent_id": str(agent_id),
    #             "agent_name": name
    #         }
    #     )

    #     return config

    # def remove_sub_agent(
    #     self,
    #     app_id: uuid.UUID,
    #     agent_id: uuid.UUID
    # ) -> MultiAgentConfig:
    #     """移除子 Agent

    #     Args:
    #         app_id: 应用 ID
    #         agent_id: Agent ID

    #     Returns:
    #         更新后的配置
    #     """
    #     config = self.get_config(app_id)
    #     if not config:
    #         raise ResourceNotFoundException("多 Agent 配置", str(app_id))

    #     # 查找并移除
    #     original_count = len(config.sub_agents)
    #     config.sub_agents = [
    #         sub_agent for sub_agent in config.sub_agents
    #         if sub_agent["agent_id"] != str(agent_id)
    #     ]

    #     if len(config.sub_agents) == original_count:
    #         raise ResourceNotFoundException("子 Agent", str(agent_id))

    #     # 标记为已修改
    #     self.db.add(config)
    #     self.db.commit()
    #     self.db.refresh(config)

    #     logger.info(
    #         "移除子 Agent 成功",
    #         extra={
    #             "config_id": str(config.id),
    #             "agent_id": str(agent_id)
    #         }
    #     )

    #     return config

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

# ==================== 依赖注入函数 ====================

def get_multi_agent_service(
        db: Annotated[Session, Depends(get_db)]
) -> MultiAgentService:
    """获取工作流服务（依赖注入）"""
    return MultiAgentService(db)
