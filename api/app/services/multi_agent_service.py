"""多 Agent 配置管理服务"""
import uuid
import json
from typing import AsyncGenerator, Optional, List, Tuple, Any, Annotated

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

    def __init__(self, db: Session | AsyncSession):
        self.db = db

    def _uses_async_session(self) -> bool:
        return isinstance(self.db, AsyncSession)

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
            model_api_key = ModelApiKeyService.get_available_api_key(
                self.db,
                data.default_model_config_id,
                tenant_id=tenant_id,
            )
            if not model_api_key:
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
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        # S1 轮次锚点：预生成 assistant 消息 ID，下传给编排器写 master 执行记录，
        # 落库用同一 ID，日志详情按消息挂载节点不依赖时序吸附。
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

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
            await BatchPersistQueue.enqueue(PersistTask(
                task_type="save_messages",
                args={
                    "conversation_id": str(request.conversation_id),
                    "result": _result,
                    "user_message_id_override": user_message_id,
                    "user_message_content": request.message,
                    "should_memorize": orchestrator.cluster_memory_enabled(),
                },
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
        config = await self.get_config_async(app_id)
        if not config:
            raise ResourceNotFoundException("多 Agent 配置", str(app_id))

        if not config.is_active:
            raise BusinessException("多 Agent 配置已禁用", BizCode.NOT_FOUND)

        # 2. 创建编排器
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        full_content = ""
        total_tokens = 0
        # 本轮 assistant 消息 ID（S1 轮次锚点）：预先生成并下传给编排器，
        # master 执行记录据此带 message_id（经 BatchPersistQueue 回填），
        # 日志详情按消息挂载节点时不再依赖"时序就近"兜底——
        # 兜底在多轮/子 Agent 并发场景下会把同一轮的节点吸附到别的消息，
        # 表现为"一轮会话分散到多条日志消息"。
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

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
        await BatchPersistQueue.enqueue(PersistTask(
            task_type="save_messages",
            args={
                "conversation_id": str(request.conversation_id),
                "result": _result,
                "user_message_id_override": user_message_id,
                "user_message_content": request.message,
                "should_memorize": orchestrator.cluster_memory_enabled(),
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
