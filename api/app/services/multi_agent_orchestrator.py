"""多 Agent 编排器 - Master Agent 作为决策中心"""
import uuid
import time
import json
import asyncio
import inspect
from typing import Dict, Any, List, Optional, AsyncIterator, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import MultiAgentConfig, AgentConfig, ModelConfig
from app.models.multi_agent_model import AggregationStrategy, OrchestrationMode
from app.services.agent_registry import AgentRegistry
from app.services.master_agent_router import MasterAgentRouter
from app.services.conversation_state_manager import ConversationStateManager
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.repositories.tool_repository import ToolRepository
from app.services.model_service import ModelApiKeyService

logger = get_business_logger()

# 集群运行中"可观测事件"白名单：这些事件没有 content，会被子 Agent 事件解析循环
# 当成"非内容事件"丢弃，导致运行中面板拿不到轨迹 / 派发收尾。
# 只放真正驱动 UI 的四个事件：轨迹快照（agent_log/agent_log_final）+ 区块建/收
# （agent_dispatch/agent_complete）。tool_start/tool_end 不转发 —— agent_log 的
# trace 里已含 tool_calls，重复转发只是徒增 SSE 流量。
# 其余事件（尤其 sub_usage）保持原样过滤，避免改变既有的 token 汇总口径。
_CLUSTER_OBSERVABILITY_EVENTS = frozenset({
    "agent_log",
    "agent_log_final",
    "agent_dispatch",
    "agent_complete",
})

# 子运行（子 Agent / handoffs）自己的 start / end 属于"子运行生命周期"，不是集群会话事件。
# 若原样透传给上层会同时踩两个坑：
#  1) 一次集群会话出现两个 start、两个 end，上层（前端/外部 App API）无法按 start…end 界定一轮；
#  2) 子运行 start 里的 message_id / user_message_id 是 run_stream 内部 uuid4 生成的临时 id
#     （子运行 skip_save，不落库），前端 applyModelMessageId 会用它顶掉集群气泡的真实 id，
#     导致复制/重新生成/反馈等按 message_id 定位的动作指向一条不存在的消息。
# 子运行的可见性由 agent_log / agent_dispatch / agent_complete 承担，与白名单路径保持一致。
_SUB_RUN_LIFECYCLE_EVENTS = frozenset({"start", "end"})


def _sse_event_name(event: str) -> Optional[str]:
    """取 SSE 字符串的 event 名（解析失败返回 None）。"""
    if not isinstance(event, str) or not event.startswith("event:"):
        return None
    try:
        return event.split("event:", 1)[1].split("\n", 1)[0].strip() or None
    except Exception:
        return None


class MultiAgentOrchestrator:
    """多 Agent 编排器 - 协调多个 Agent 协作完成任务"""

    def __init__(self, db: Session | AsyncSession, config: MultiAgentConfig):
        """初始化编排器

        Args:
            db: 数据库会话
            config: 多 Agent 配置
        """
        self.db = db
        self.config = config
        self.registry = AgentRegistry(db)

        # 兼容处理：旧的 orchestration_mode 值映射到新值
        # collaboration | supervisor 是新值，其他旧值默认使用 supervisor
        self._normalized_mode = self._normalize_orchestration_mode(config.orchestration_mode)

        # 加载主 Agent
        # self.master_agent = self._load_agent(config.master_agent_id)
        # self. config.d
        self.default_model_config_id = config.default_model_config_id
        self.model_parameters = config.model_parameters
        self.tenant_id = None
        self.sub_agents = {}
        self.state_manager = ConversationStateManager()
        self.master_model_config = None
        self.router = None

        # ── 集群执行归属（多 Agent 日志）────────────────────────────────
        # 主（编排）Agent 的 execution id：子 Agent 落库时作为 parent_execution_id。
        # 用实例属性承载而不是逐调用点传参 —— _execute_sub_agent_stream 有 4 个调用点、
        # _execute_sub_agent 有 10 个，逐个加参数必然漏改（上次流式路径漏传炸过外键）。
        self.current_execution_id: Optional[uuid.UUID] = None
        self.current_conversation_id: Optional[uuid.UUID] = None
        self._master_started_at: Optional[float] = None

    @staticmethod
    async def _maybe_await(value):
        if inspect.isawaitable(value):
            return await value
        return value

    async def _db_get(self, model, identity):
        if self.db is None:
            return None
        return await self._maybe_await(self.db.get(model, identity))

    # ──────────────────────────────────────────────────────────────────
    # 多 Agent 日志：主执行记录的生命周期
    # ──────────────────────────────────────────────────────────────────

    async def _ensure_master_execution(
        self,
        conversation_id: Optional[uuid.UUID],
        message_id: Optional[uuid.UUID] = None,
    ) -> Optional[uuid.UUID]:
        """为主（编排）Agent 建一条 agent_executions 记录。

        多 Agent 会话此前**完全没有** execution 记录（app_chat_service 直接调 orchestrator，
        只入队消息落库），日志详情里的节点无处挂载，子 Agent 也找不到 parent。
        这里统一补上；子 Agent 落库时以它的 id 作为 parent_execution_id。

        为什么创建时不写 message_id：本轮 assistant 消息由 BatchPersistQueue 异步落库
        （晚于本方法执行），此刻 messages 行还不存在，直接写会触发 FK 违例并把整条
        观测链路降级掉。message_id 改由 app_chat_service 在消息落库后
        （batch 内 save_messages 之后）回填 —— 见 PersistTask "link_agent_execution_message"。

        观测失败不阻断对话：任何异常只记 warning，current_execution_id 保持 None
        （此时子 Agent 以 parent_execution_id=None 落库，详情侧按"同会话 + 时序就近"兜底）。
        """
        self.current_conversation_id = conversation_id
        self._master_started_at = time.time()
        if self.db is None or not getattr(self.config, "app_id", None) or not conversation_id:
            return None
        try:
            from app.services.draft_run_service import AgentRunService

            conv_uuid = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
            svc = AgentRunService(self.db)
            self.current_execution_id = await svc._create_agent_execution_async(
                app_id=self.config.app_id,
                conversation_id=conv_uuid,
                # 主 Agent 拿不到 agent_configs.id：config.master_agent_id 本身就是 release id
                agent_config_id=None,
                started_at=utcnow_naive(),
                model_name=(self.master_model_config.name if self.master_model_config else "") or "",
                provider=None,
                agent_role="master",
                parent_execution_id=None,
                orchestration_mode=self._normalized_mode,
                release_id=self.config.master_agent_id,
                agent_name=self.config.master_agent_name or "主编排",
                message_id=None,
            )
            logger.info(
                "集群主执行记录已创建",
                extra={
                    "execution_id": str(self.current_execution_id),
                    "conversation_id": str(conv_uuid),
                    "mode": self._normalized_mode,
                    "pending_message_id": str(message_id) if message_id else None,
                },
            )
        except Exception as e:
            # 观测链路故障不影响对话
            self.current_execution_id = None
            logger.warning(f"创建集群主执行记录失败（已降级，不影响对话）: {e}")
        return self.current_execution_id

    async def _finalize_master_execution(
        self,
        status: str = "completed",
        elapsed_time: Optional[float] = None,
        token_usage: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """收尾主 execution 记录；失败同样只记 warning。"""
        execution_id = self.current_execution_id
        if execution_id is None or self.db is None:
            return
        try:
            from app.services.draft_run_service import AgentRunService

            svc = AgentRunService(self.db)
            await svc._update_agent_execution_completed_async(
                execution_id=execution_id,
                steps=[],
                status=status,
                elapsed_time=elapsed_time,
                token_usage=token_usage,
                error_message=error_message,
            )
        except Exception as e:
            logger.warning(f"收尾集群主执行记录失败（已降级）: {e}")

    @staticmethod
    def _parse_sse_event(event: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """解析 SSE 字符串为 (事件名, data)。解析失败返回 (None, {})，调用方按"透传"处理。"""
        try:
            name = None
            for line in event.splitlines():
                if line.startswith("event:"):
                    name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    return name, payload if isinstance(payload, dict) else {}
            return name, {}
        except Exception:
            return None, {}

    async def _open_collaboration_activation(
        self,
        agent_configs: Dict[str, Any],
        agent_key: Optional[str],
        display_name: Optional[str],
        message: str,
    ) -> Tuple[Optional[uuid.UUID], Dict[str, Any]]:
        """协作模式：按"节点激活"开一条子 Agent 执行记录。

        协作模式主链路是 handoffs_service（LangGraph handoff），**不经过**
        AgentRunService.run_stream，所以不会自动产生子执行记录，必须在此手动补。
        同一 Agent 多次激活 → 多行，execution_id 各不相同。
        """
        info = agent_configs.get(agent_key or "", {}) or {}
        meta = {
            "execution_id": None,
            "agent_id": info.get("agent_id") or agent_key,
            "agent_name": display_name or info.get("name") or agent_key,
            "parent_execution_id": str(self.current_execution_id) if self.current_execution_id else None,
            "orchestration_mode": self._normalized_mode,
            "task": (message or "")[:500],
        }
        if self.db is None or not getattr(self.config, "app_id", None) or not self.current_conversation_id:
            return None, meta
        try:
            from app.services.draft_run_service import AgentRunService

            release_id = None
            raw_agent_id = info.get("agent_id")
            if raw_agent_id:
                try:
                    release_id = raw_agent_id if isinstance(raw_agent_id, uuid.UUID) else uuid.UUID(str(raw_agent_id))
                except (ValueError, TypeError):
                    release_id = None

            svc = AgentRunService(self.db)
            execution_id = await svc._create_agent_execution_async(
                app_id=self.config.app_id,
                conversation_id=self.current_conversation_id,
                agent_config_id=None,
                started_at=utcnow_naive(),
                model_name="",
                provider=None,
                agent_role="sub",
                parent_execution_id=self.current_execution_id,
                orchestration_mode=self._normalized_mode,
                release_id=release_id,
                agent_name=meta["agent_name"],
                agent_id=str(info.get("agent_id") or agent_key) if (info.get("agent_id") or agent_key) else None,
                # 与主管模式同口径：把激活时收到的任务落进 meta_data，
                # 详情页的"输入"因此不依赖 steps 的形态（steps 里虽也有 head，但统一走 meta.task 更稳）
                task=meta["task"],
            )
            meta["execution_id"] = str(execution_id)
            return execution_id, meta
        except Exception as e:
            logger.warning(f"协作模式子 Agent 记录创建失败（已降级，不影响对话）: {e}")
            return None, meta

    async def _close_collaboration_activation(
        self,
        execution_id: Optional[uuid.UUID],
        status: str,
        elapsed_time: Optional[float] = None,
        token_usage: Optional[dict] = None,
        error_message: Optional[str] = None,
        steps: Optional[list] = None,
    ) -> None:
        """协作模式：收尾一条子 Agent 激活记录。

        steps 由调用方按激活粒度组装（该 Agent 的输入/产出 + 它发起的 handoff），
        因为协作模式没有 AgentTraceRecorder 的 trace，steps 就是它的"调用链明细"。
        """
        if execution_id is None or self.db is None:
            return
        try:
            from app.services.draft_run_service import AgentRunService

            svc = AgentRunService(self.db)
            await svc._update_agent_execution_completed_async(
                execution_id=execution_id,
                steps=steps or [],
                status=status,
                elapsed_time=elapsed_time,
                token_usage=token_usage,
                error_message=error_message,
            )
        except Exception as e:
            logger.warning(f"协作模式子 Agent 记录收尾失败（已降级）: {e}")

    def _sub_event_meta(self, agent_id: Optional[str], agent_name: Optional[str]) -> Dict[str, Any]:
        """运行中子 Agent 事件的归属字段。

        不含 execution_id：子执行记录由 run_stream 内部创建，编排层在此刻还不知道 id；
        前端先按 agent_id/agent_name 绑定区块，随后由 agent_dispatch / agent_complete
        里携带的 execution_id 回填（协作模式同一 Agent 多次激活时，execution_id 是唯一键）。
        """
        return {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "parent_execution_id": str(self.current_execution_id) if self.current_execution_id else None,
            "orchestration_mode": self._normalized_mode,
        }

    def _inject_agent_meta(self, event: str, meta: Dict[str, Any]) -> str:
        """向 SSE 事件的 data 注入 Agent 归属字段，事件名与事件类型保持不变。

        只改 `data:` 行（json.loads → setdefault → json.dumps），`event:` 行与结尾空行原样保留；
        用 setdefault 保证不覆盖事件自带的同名字段（例如 handoffs 的 agent_name）。
        """
        if not isinstance(event, str) or "data:" not in event:
            return event
        try:
            head, _, payload = event.partition("data:")
            data = json.loads(payload.strip())
            if not isinstance(data, dict):
                return event
            for k, v in (meta or {}).items():
                if v is not None:
                    data.setdefault(k, v)
            return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception:
            return event

    def _sub_agent_message_event(
        self,
        agent_id: Optional[str],
        agent_name: Optional[str],
        content: str,
        **extra: Any,
    ) -> str:
        """子 Agent 正文事件（**聚合下发**：整段正文一次发出，不再逐 token 分片）。

        正文在子 Agent 运行期间只做累积，等该 Agent 跑完再发一次。理由：
        1) 该事件无增量语义 —— 消费方拿到分片除了拼接别无用途，一次集群会话
           却因此多出成百上千个事件；
        2) 运行中面板的"实时观感"由 `agent_log` 轨迹快照承担（每轮 llm/tool 结束
           各推一份全量快照），与 `sub_agent_message` 无关；
        3) 前端 `streamHandlers.ts` 本就不消费该事件，改动对 UI 无影响。

        每次仍需 new 事件（同一 Agent 多次被调用时内容不同），
        故按"一次执行一次事件"的粒度发出。
        """
        payload: Dict[str, Any] = {
            "content": content,
            "agent_id": agent_id,
            "agent_name": agent_name,
        }
        payload.update({k: v for k, v in (extra or {}).items() if v is not None})
        return self._format_sse_event("sub_agent_message", payload)

    @classmethod
    async def create(cls, db: Session | AsyncSession | None, config: MultiAgentConfig) -> "MultiAgentOrchestrator":
        orchestrator = cls(db, config)

        if config.app_id and db is not None:
            from app.models import App
            if isinstance(db, AsyncSession):
                app = await db.get(App, config.app_id)
            else:
                app = db.get(App, config.app_id)
            if app and app.workspace_id:
                if isinstance(db, AsyncSession):
                    orchestrator.tenant_id = await ToolRepository.get_tenant_id_by_workspace_id_async(
                        db,
                        str(app.workspace_id),
                    )
                else:
                    orchestrator.tenant_id = ToolRepository.get_tenant_id_by_workspace_id(
                        db,
                        str(app.workspace_id),
                    )

        for sub_agent_info in config.sub_agents:
            agent_id = uuid.UUID(sub_agent_info["agent_id"])
            agent = await orchestrator._load_agent_async(agent_id)
            orchestrator.sub_agents[str(agent_id)] = {
                "config": agent,
                "info": sub_agent_info
            }

        if orchestrator._normalized_mode == OrchestrationMode.SUPERVISOR:
            if not orchestrator.default_model_config_id:
                raise BusinessException("Supervisor 模式需要配置默认模型", BizCode.AGENT_CONFIG_MISSING)

            orchestrator.master_model_config = await orchestrator._db_get(
                ModelConfig,
                orchestrator.default_model_config_id,
            )
            if not orchestrator.master_model_config:
                raise BusinessException("Master Agent 模型配置不存在", BizCode.AGENT_CONFIG_MISSING)

            orchestrator.router = MasterAgentRouter(
                db=db,
                master_model_config=orchestrator.master_model_config,
                model_parameters=orchestrator.model_parameters,
                sub_agents=orchestrator.sub_agents,
                state_manager=orchestrator.state_manager,
                tenant_id=orchestrator.tenant_id,
                enable_rule_fast_path=config.execution_config.get("enable_rule_fast_path", True)
            )
        logger.info(
            "多 Agent 编排器初始化完成",
            extra={
                "config_id": str(config.id),
                "model": orchestrator.master_model_config.name if orchestrator.master_model_config else None,
                "sub_agent_count": len(orchestrator.sub_agents),
                "orchestration_mode": orchestrator._normalized_mode
            }
        )

        return orchestrator

    def _normalize_orchestration_mode(self, mode: str) -> str:
        """标准化 orchestration_mode，兼容旧值

        Args:
            mode: 原始的 orchestration_mode 值

        Returns:
            标准化后的模式：collaboration 或 supervisor
        """
        if mode in [OrchestrationMode.SUPERVISOR, "supervisor"]:
            return OrchestrationMode.SUPERVISOR
        # 其他所有值（包括旧的 sequential、parallel、conditional、loop 和 collaboration）都映射到 collaboration
        return OrchestrationMode.COLLABORATION

    async def execute_stream(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        use_llm_routing: bool = True,
        web_search: bool = True,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        message_id: Optional[uuid.UUID] = None
    ):
        """执行多 Agent 任务（流式返回）

        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            use_llm_routing: 是否使用 LLM 路由
            web_search: 是否启用网络搜索
            memory: 是否启用记忆功能
            storage_type: 存储类型
            user_rag_memory_id: 用户 RAG 记忆 ID
            message_id: 本轮 assistant 消息 ID（写入主执行记录，供日志详情按消息挂载节点）

        Yields:
            SSE 格式的事件流
        """

        start_time = time.time()

        logger.info(
            "开始执行多 Agent 任务（流式）",
            extra={
                "mode": self._normalized_mode,
                "message_length": len(message)
            }
        )

        # 主执行记录：子 Agent 记录需要它作为 parent，日志详情也需要它来挂载节点
        await self._ensure_master_execution(conversation_id, message_id)

        try:
            # 发送开始事件
            yield self._format_sse_event("start", {
                "mode": self._normalized_mode,
                "timestamp": time.time()
            })

            # 2. 根据模式执行（流式）
            # Collaboration 模式：Agent 之间可以相互 handoff（使用 handoffs_service）
            if self._normalized_mode == OrchestrationMode.COLLABORATION:
                async for event in self._execute_collaboration_mode_stream(
                    message,
                    conversation_id,
                    user_id,
                    web_search,
                    memory,
                    storage_type,
                    user_rag_memory_id
                ):
                    yield event
            # Supervisor 模式：由主 Agent 统一调度子 Agent
            elif self._normalized_mode == OrchestrationMode.SUPERVISOR:
                # 1. 主 Agent 分析任务
                task_analysis = await self._analyze_task(message, variables)
                task_analysis["use_llm_routing"] = use_llm_routing

                async for event in self._execute_supervisor_stream(
                    task_analysis,
                    conversation_id,
                    user_id,
                    web_search,
                    memory,
                    storage_type,
                    user_rag_memory_id
                ):
                    yield event
            else:
                raise BusinessException(
                    f"不支持的编排模式: {self._normalized_mode}",
                    BizCode.INVALID_PARAMETER
                )

            elapsed_time = time.time() - start_time

            # 发送结束事件
            yield self._format_sse_event("end", {
                "elapsed_time": elapsed_time,
                "timestamp": time.time()
            })

            logger.info(
                "多 Agent 任务完成（流式）",
                extra={
                    "mode": self._normalized_mode,
                    "elapsed_time": elapsed_time
                }
            )

            await self._finalize_master_execution(status="completed", elapsed_time=time.time() - start_time)

        except Exception as e:
            logger.error(
                "多 Agent 任务执行失败（流式）",
                extra={"error": str(e), "mode": self._normalized_mode},
                exc_info=True
            )
            await self._finalize_master_execution(
                status="failed",
                elapsed_time=time.time() - start_time,
                error_message=str(e)[:2000],
            )
            # 发送错误事件
            yield self._format_sse_event("error", {
                "error": str(e),
                "timestamp": time.time()
            })

    async def execute(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        use_llm_routing: bool = True,
        web_search: bool = False,
        memory: bool = True,
        message_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """执行多 Agent 任务（基于 Master Agent 决策）

        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            use_llm_routing: 是否使用 LLM 路由（保留参数，实际总是使用 Master Agent）
            message_id: 本轮 assistant 消息 ID（写入主执行记录）

        Returns:
            执行结果
        """
        start_time = time.time()

        logger.info(
            "开始执行多 Agent 任务",
            extra={
                "message_length": len(message),
                "mode": self._normalized_mode
            }
        )

        await self._ensure_master_execution(conversation_id, message_id)

        try:
            # Collaboration 模式：使用 handoffs_service
            if self._normalized_mode == OrchestrationMode.COLLABORATION:
                collab_result = await self._execute_collaboration_mode(
                    message,
                    conversation_id,
                    user_id,
                    variables
                )
                await self._finalize_master_execution(
                    status="completed",
                    elapsed_time=time.time() - start_time,
                    token_usage=(collab_result or {}).get("usage"),
                )
                return collab_result

            # Supervisor 模式：由 Master Agent 统一调度
            # 1. Master Agent 分析任务并做出决策
            task_analysis = await self._analyze_task(message, variables)

            routing_decision = task_analysis.get("routing_decision")
            if not routing_decision:
                raise BusinessException("Master Agent 未返回路由决策", BizCode.AGENT_CONFIG_MISSING)

            logger.info(
                "Master Agent 决策",
                extra={
                    "need_collaboration": routing_decision.get("need_collaboration"),
                    "strategy": routing_decision.get("collaboration_strategy"),
                    "confidence": routing_decision.get("confidence")
                }
            )

            # 2. 根据 Master Agent 的决策执行
            results = await self._execute_conditional(
                task_analysis,
                conversation_id,
                user_id
            )

            # 3. 整合结果
            final_result = await self._aggregate_results(results)

            elapsed_time = time.time() - start_time

            # 4. 提取子 Agent 的 conversation_id（用于多轮对话）
            sub_conversation_id = None
            total_tokens = 0
            
            # 累加 Master Agent 路由决策消耗的 token
            total_tokens += task_analysis.get("routing_tokens", 0)
            # 累加 Master Agent 整合消耗的 token
            total_tokens += getattr(self, '_last_merge_tokens', 0)

            if isinstance(results, dict):
                sub_conversation_id = results.get("conversation_id") or results.get("result", {}).get("conversation_id")
                # 提取 token 信息
                usage = results.get("usage", {}) or results.get("result", {}).get("usage", {})
                total_tokens += usage.get("total_tokens", 0)
            elif isinstance(results, list) and results:
                for item in results:
                    if "result" in item:
                        sub_conversation_id = item["result"].get("conversation_id")
                        if sub_conversation_id:
                            break
                    # 累加每个子 Agent 的 token
                    usage = item.get("usage", {}) or item.get("result", {}).get("usage", {})
                    total_tokens += usage.get("total_tokens", 0)

            logger.info(
                "多 Agent 任务完成",
                extra={
                    "strategy": routing_decision.get("collaboration_strategy", "single"),
                    "elapsed_time": elapsed_time,
                    "sub_conversation_id": sub_conversation_id
                }
            )

            await self._finalize_master_execution(
                status="completed",
                elapsed_time=elapsed_time,
                token_usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": total_tokens
                },
            )

            return {
                "message": final_result,
                "conversation_id": sub_conversation_id,
                "mode": OrchestrationMode.SUPERVISOR,
                "elapsed_time": elapsed_time,
                "strategy": routing_decision.get("collaboration_strategy", "single"),
                "sub_results": results,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": total_tokens
                }
            }

        except Exception as e:
            logger.error(
                "多 Agent 任务执行失败",
                extra={"error": str(e)}
            )
            await self._finalize_master_execution(
                status="failed",
                elapsed_time=time.time() - start_time,
                error_message=str(e)[:2000],
            )
            raise

    async def _analyze_task(
        self,
        message: str,
        variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Master Agent 分析任务并做出路由决策

        Args:
            message: 用户消息
            variables: 变量参数

        Returns:
            任务分析结果，包含路由决策
        """
        logger.info(
            "Master Agent 开始分析任务",
            extra={"message_length": len(message)}
        )

        # 使用 Master Agent 路由器进行决策
        routing_decision = await self.router.route(
            message=message,
            conversation_id=None,  # 会在后续传入
            variables=variables
        )

        # 获取路由决策消耗的 token
        routing_tokens = getattr(self.router, '_last_routing_tokens', 0)

        logger.info(
            "Master Agent 分析完成",
            extra={
                "selected_agent": routing_decision.get("selected_agent_id"),
                "confidence": routing_decision.get("confidence"),
                "strategy": routing_decision.get("strategy"),
                "routing_tokens": routing_tokens
            }
        )

        return {
            "message": message,
            "variables": variables or {},
            "sub_agents": self.config.sub_agents,
            "initial_context": variables or {},
            "routing_decision": routing_decision,
            "routing_tokens": routing_tokens
        }

    async def _execute_sequential(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> List[Dict[str, Any]]:
        """顺序执行子 Agent

        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID

        Returns:
            执行结果列表
        """
        results = []
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")

        # 按优先级排序
        sub_agents = sorted(
            task_analysis["sub_agents"],
            key=lambda x: x.get("priority", 0)
        )

        for sub_agent_info in sub_agents:
            agent_id = sub_agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)

            if not agent_data:
                logger.warning(f"子 Agent 不存在: {agent_id}")
                continue

            logger.info(
                "执行子 Agent",
                extra={
                    "agent_id": agent_id,
                    "agent_name": sub_agent_info.get("name"),
                    "priority": sub_agent_info.get("priority")
                }
            )

            # 执行子 Agent
            result = await self._execute_sub_agent(
                agent_data["config"],
                message,
                context,
                conversation_id,
                user_id,
                web_search,
                memory,
                storage_type,
                user_rag_memory_id
            )

            results.append({
                "agent_id": agent_id,
                "agent_name": sub_agent_info.get("name"),
                "result": result,
                "conversation_id": result.get("conversation_id")  # 保存会话 ID
            })

            # 更新上下文（后续 Agent 可以使用前面的结果）
            context[f"result_from_{sub_agent_info.get('name', agent_id)}"] = result.get("message")

        return results

    async def _execute_parallel(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> List[Dict[str, Any]]:
        """并行执行子 Agent

        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID

        Returns:
            执行结果列表
        """
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")

        # 获取并发限制
        parallel_limit = self.config.execution_config.get("parallel_limit", 3)

        # 创建任务列表
        tasks = []
        for sub_agent_info in task_analysis["sub_agents"]:
            agent_id = sub_agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)

            if not agent_data:
                continue

            task = self._execute_sub_agent(
                agent_data["config"],
                message,
                context,
                conversation_id,
                user_id,
                web_search,
                memory,
                storage_type,
                user_rag_memory_id
            )
            tasks.append((agent_id, sub_agent_info.get("name"), task))

        # 并行执行（带限制）
        results = []
        for i in range(0, len(tasks), parallel_limit):
            batch = tasks[i:i + parallel_limit]
            batch_results = await asyncio.gather(
                *[task for _, _, task in batch],
                return_exceptions=True
            )

            for (agent_id, agent_name, _), result in zip(batch, batch_results, strict=False):
                if isinstance(result, Exception):
                    logger.error(f"子 Agent 执行失败: {agent_name}", extra={"error": str(result)})
                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "error": str(result)
                    })
                else:
                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "result": result,
                        "conversation_id": result.get("conversation_id")  # 保存会话 ID
                    })

        return results

    async def _execute_collaboration_stream(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ):
        """多 Agent 协作流式执行

        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID
            routing_decision: 路由决策

        Yields:
            SSE 格式的事件流
        """
        message = task_analysis.get("message", "")
        initial_context = task_analysis.get("initial_context", {})
        collaboration_strategy = routing_decision.get("collaboration_strategy", "sequential")

        # 获取协作信息
        if collaboration_strategy == "decomposition":
            collaboration_agents = routing_decision.get("sub_questions", [])
        else:
            collaboration_agents = routing_decision.get("collaboration_agents", [])

        logger.info(
            "开始流式协作执行",
            extra={
                "strategy": collaboration_strategy,
                "agent_count": len(collaboration_agents)
            }
        )

        # 1. 发送编排计划事件（在执行前）
        # 构建子任务信息
        sub_tasks = []
        for item in collaboration_agents:
            if collaboration_strategy == "decomposition":
                # 问题拆分模式
                agent_id = item.get("agent_id")
                agent_data = self.sub_agents.get(agent_id)
                if agent_data:
                    sub_tasks.append({
                        "agent_id": agent_id,
                        "agent_name": agent_data.get("info", {}).get("name", agent_id),
                        "sub_question": item.get("question", ""),
                        "order": item.get("order", 0)
                    })
            else:
                # 其他协作模式
                agent_id = item.get("agent_id")
                agent_data = self.sub_agents.get(agent_id)
                if agent_data:
                    sub_tasks.append({
                        "agent_id": agent_id,
                        "agent_name": agent_data.get("info", {}).get("name", agent_id),
                        "role": item.get("role", "secondary"),
                        "order": item.get("order", 0)
                    })

        yield self._format_sse_event("orchestration_plan", {
            "agent_count": len(sub_tasks),
            "strategy": collaboration_strategy,
            "sub_tasks": sub_tasks
        })

        # 2. 流式执行所有子 Agent
        results = []

        # 获取执行模式配置
        execution_mode = self.config.execution_config.get("sub_agent_execution_mode", "parallel")

        if collaboration_strategy == "decomposition":
            # 问题拆分模式
            # 检查是否有依赖关系
            has_dependencies = self._check_dependencies(collaboration_agents)

            if has_dependencies or execution_mode == "sequential":
                # 有依赖或配置为串行：串行流式执行
                logger.info("使用串行流式执行（问题拆分）")
                for sub_q in sorted(collaboration_agents, key=lambda x: x.get("order", 0)):
                    sub_question = sub_q.get("question", "")
                    agent_id = sub_q.get("agent_id")

                    agent_data = self.sub_agents.get(agent_id)
                    if not agent_data:
                        continue

                    agent_name = agent_data.get("info", {}).get("name", agent_id)

                    # 发送子问题开始事件
                    yield self._format_sse_event("sub_question_start", {
                        "question": sub_question,
                        "agent_name": agent_name
                    })

                    # 流式执行子 Agent，收集结果
                    result_content = ""
                    async for event in self._execute_sub_agent_stream(
                        agent_data["config"],
                        sub_question,
                        initial_context,
                        conversation_id,
                        user_id
                    ):
                        # 解析原始事件
                        if "data:" in event:
                            try:
                                import json
                                data_line = event.split("data: ", 1)[1].strip()
                                data = json.loads(data_line)

                                # 提取内容：只累积，不下发（聚合后一次性发出）
                                if "content" in data:
                                    result_content += data["content"]
                                elif _sse_event_name(event) in _CLUSTER_OBSERVABILITY_EVENTS:
                                    # 集群可观测事件（工具时序 / 轨迹快照 / 派发收尾）没有 content，
                                    # 必须原样透传并补归属，否则运行中面板看不到子 Agent 的调用链
                                    yield self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_name))
                            except Exception:
                                pass
                        else:
                            # 非 data 事件直接转发
                            yield event

                    # 子 Agent 正文聚合下发（本 Agent 跑完才发，全文一次到达）
                    if result_content:
                        yield self._sub_agent_message_event(
                            agent_id, agent_name, result_content, sub_question=sub_question
                        )

                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "sub_question": sub_question,
                        "result": {"message": result_content}
                    })

                    # 发送子问题完成事件
                    yield self._format_sse_event("sub_question_end", {
                        "agent_name": agent_name
                    })
            else:
                # 无依赖且配置为并行：并行流式执行
                logger.info(f"使用并行流式执行（问题拆分），共 {len(collaboration_agents)} 个子问题")

                # 准备并行任务
                agent_tasks = []
                agent_info_map = {}
                result_contents = {}

                for sub_q in collaboration_agents:
                    sub_question = sub_q.get("question", "")
                    agent_id = sub_q.get("agent_id")

                    agent_data = self.sub_agents.get(agent_id)
                    if not agent_data:
                        continue

                    agent_name = agent_data.get("info", {}).get("name", agent_id)
                    agent_info_map[agent_id] = {
                        "name": agent_name,
                        "sub_question": sub_question
                    }
                    result_contents[agent_id] = ""

                    agent_tasks.append((
                        agent_id,
                        agent_name,
                        agent_data["config"],
                        sub_question,
                        initial_context
                    ))

                    # 发送子问题开始事件
                    yield self._format_sse_event("sub_question_start", {
                        "question": sub_question,
                        "agent_name": agent_name
                    })

                # 并行流式执行
                async for agent_id, agent_name, event_type, content in self._parallel_stream_agents(
                    agent_tasks,
                    conversation_id,
                    user_id
                ):
                    if event_type == "content":
                        # 累积结果（不下发，等该 Agent done 时聚合发出）
                        result_contents[agent_id] += content

                    elif event_type == "raw":
                        # 子 Agent 可观测事件（工具 / 轨迹 / 派发收尾）：原样透传
                        yield content

                    elif event_type == "done":
                        # Agent 完成：先聚合下发正文（全文一次到达），再收尾
                        if result_contents[agent_id]:
                            yield self._sub_agent_message_event(
                                agent_id,
                                agent_name,
                                result_contents[agent_id],
                                sub_question=agent_info_map[agent_id]["sub_question"],
                            )

                        results.append({
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "sub_question": agent_info_map[agent_id]["sub_question"],
                            "result": {"message": result_contents[agent_id]}
                        })

                        yield self._format_sse_event("sub_question_end", {
                            "agent_name": agent_name
                        })

                    elif event_type == "error":
                        logger.error(f"Agent {agent_name} 执行失败: {content}")
                        # 失败前已生成的部分正文仍要交付，否则聚合后彻底不可见
                        if result_contents.get(agent_id):
                            yield self._sub_agent_message_event(
                                agent_id,
                                agent_name,
                                result_contents[agent_id],
                                sub_question=agent_info_map[agent_id]["sub_question"],
                            )
        else:
            # 其他协作模式（sequential/parallel/hierarchical）
            if collaboration_strategy == "parallel" and execution_mode == "parallel":
                # 并行协作 + 并行流式执行
                logger.info(f"使用并行流式执行（并行协作），共 {len(collaboration_agents)} 个 Agent")

                # 准备并行任务
                agent_tasks = []
                agent_info_map = {}
                result_contents = {}

                for agent_info in collaboration_agents:
                    agent_id = agent_info.get("agent_id")
                    agent_data = self.sub_agents.get(agent_id)
                    if not agent_data:
                        continue

                    agent_name = agent_data.get("info", {}).get("name", agent_id)
                    agent_info_map[agent_id] = {
                        "name": agent_name,
                        "role": agent_info.get("role", "secondary"),
                        "task": agent_info.get("task", "")
                    }
                    result_contents[agent_id] = ""

                    # 构建该 Agent 的消息
                    agent_task = agent_info.get("task", "处理任务")
                    agent_message = f"""原始问题：{message}

你的任务：{agent_task}

请完成你的任务。"""

                    agent_tasks.append((
                        agent_id,
                        agent_name,
                        agent_data["config"],
                        agent_message,
                        initial_context.copy()
                    ))

                    # 发送 Agent 开始事件
                    yield self._format_sse_event("agent_start", {
                        "agent_name": agent_name
                    })

                # 并行流式执行
                async for agent_id, agent_name, event_type, content in self._parallel_stream_agents(
                    agent_tasks,
                    conversation_id,
                    user_id
                ):
                    if event_type == "content":
                        # 累积结果（不下发，等该 Agent done 时聚合发出）
                        result_contents[agent_id] += content

                    elif event_type == "raw":
                        # 子 Agent 可观测事件（工具 / 轨迹 / 派发收尾）：原样透传
                        yield content

                    elif event_type == "done":
                        # Agent 完成：先聚合下发正文（全文一次到达），再收尾
                        if result_contents[agent_id]:
                            yield self._sub_agent_message_event(
                                agent_id,
                                agent_name,
                                result_contents[agent_id],
                                role=agent_info_map[agent_id]["role"],
                            )

                        results.append({
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "role": agent_info_map[agent_id]["role"],
                            "task": agent_info_map[agent_id]["task"],
                            "result": {"message": result_contents[agent_id]}
                        })

                        yield self._format_sse_event("agent_end", {
                            "agent_name": agent_name
                        })

                    elif event_type == "error":
                        logger.error(f"Agent {agent_name} 执行失败: {content}")
                        # 失败前已生成的部分正文仍要交付，否则聚合后彻底不可见
                        if result_contents.get(agent_id):
                            yield self._sub_agent_message_event(
                                agent_id,
                                agent_name,
                                result_contents[agent_id],
                                role=agent_info_map[agent_id]["role"],
                            )
            else:
                # 顺序协作或层级协作 - 串行流式执行
                logger.info(f"使用串行流式执行（{collaboration_strategy}）")
                for agent_info in collaboration_agents:
                    agent_id = agent_info.get("agent_id")
                    agent_data = self.sub_agents.get(agent_id)
                    if not agent_data:
                        continue

                    agent_name = agent_data.get("info", {}).get("name", agent_id)

                    # 发送 Agent 开始事件
                    yield self._format_sse_event("agent_start", {
                        "agent_name": agent_name
                    })

                    # 流式执行子 Agent，收集结果
                    result_content = ""
                    async for event in self._execute_sub_agent_stream(
                        agent_data["config"],
                        message,
                        initial_context,
                        conversation_id,
                        user_id
                    ):
                        # 解析原始事件
                        if "data:" in event:
                            try:
                                import json
                                data_line = event.split("data: ", 1)[1].strip()
                                data = json.loads(data_line)

                                # 提取内容：只累积，不下发（聚合后一次性发出）
                                if "content" in data:
                                    result_content += data["content"]
                                elif _sse_event_name(event) in _CLUSTER_OBSERVABILITY_EVENTS:
                                    # 同 decomposition 分支：可观测事件原样透传 + 补归属
                                    yield self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_name))
                            except:
                                pass
                        else:
                            # 非 data 事件直接转发
                            yield event

                    # 子 Agent 正文聚合下发（本 Agent 跑完才发，全文一次到达）
                    if result_content:
                        yield self._sub_agent_message_event(
                            agent_id, agent_name, result_content,
                            role=agent_info.get("role", "secondary"),
                        )

                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "result": {"message": result_content}
                    })

                    # 发送 Agent 完成事件
                    yield self._format_sse_event("agent_end", {
                        "agent_name": agent_name
                    })

        # 3. 智能整合结果
        # 默认 master：由 Master Agent **流式**生成最终答案，前端主气泡逐 token 增长。
        # smart 是"不调用模型"的快速档（纯拼接），仅在下述情况兜底，但同样按块流式推送，
        # 保证任何路径都不会把整篇内容一次砸给前端（否则主气泡表现为"一坨突然出现"）。
        merge_mode = self.config.execution_config.get("result_merge_mode", "master")
        # 智能判断是否需要整合
        need_merge = self._should_merge_results(results, collaboration_strategy)

        if not need_merge:
            # 不需要 LLM 整合：用户已经在各自区块看到全部输出，
            # 这里只把汇总内容按块补进主气泡（不调用模型）
            logger.info("跳过 Master 整合阶段（直接汇总各 Agent 输出）")
            async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                yield event
        elif merge_mode == "master" and len(results) > 1:
            # Master Agent 流式整合
            logger.info("开始 Master Agent 流式整合")

            # 发送整合开始提示
            yield self._format_sse_event("merge_start", {
                "merge_mode": "master",
                "agent_count": len(results),
                "message": "正在整合多个专家的回答..."
            })

            # 流式整合
            try:
                async for event in self._master_merge_results_stream(
                    results,
                    collaboration_strategy,
                    message
                ):
                    yield event
            except Exception as e:
                logger.error(f"Master Agent 流式整合失败，降级到 smart 模式: {str(e)}")
                async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                    yield event
        else:
            # Smart 模式：不调用模型的快速整合（仍按块流式，避免主气泡一坨出现）
            logger.info("使用 Smart 模式整合")

            yield self._format_sse_event("merge_start", {
                "merge_mode": "smart",
                "agent_count": len(results)
            })

            async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                yield event

    async def _execute_supervisor_stream(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ):
        """条件路由执行（流式，重构版 - 使用 Master Agent 决策）

        Args:
            task_analysis: 任务分析结果（包含 Master Agent 的决策）
            conversation_id: 会话 ID
            user_id: 用户 ID

        Yields:
            SSE 格式的事件流
        """
        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

        message = task_analysis.get("message", "")
        routing_decision = task_analysis.get("routing_decision")
        yield self._format_sse_event("routing_decision", {
            "routing_decision": routing_decision
        })

        # 1. 检查是否需要协作
        if routing_decision and routing_decision.get("need_collaboration"):
            # 需要多 Agent 协作，使用流式整合
            logger.info("检测到需要多 Agent 协作，使用流式整合")

            async for event in self._execute_collaboration_stream(
                task_analysis,
                conversation_id,
                user_id,
                routing_decision
            ):
                yield event
            return

        # 2. 单 Agent 模式：如果有 Master Agent 的决策，直接使用
        if routing_decision and routing_decision.get("selected_agent_id"):
            agent_id = routing_decision["selected_agent_id"]

            logger.info(
                "使用 Master Agent 的路由决策（流式）",
                extra={
                    "agent_id": agent_id,
                    "confidence": routing_decision.get("confidence"),
                    "reasoning": routing_decision.get("reasoning")
                }
            )
        else:
            # 2. 降级：使用旧的路由逻辑
            logger.warning("未获取到 Master Agent 决策，使用旧路由逻辑（流式）")
            # use_llm = task_analysis.get("use_llm_routing", True)
            # selected_agent_info = await self._route_by_rules(
            #     message,
            #     task_analysis["sub_agents"],
            #     use_llm=use_llm,
            #     conversation_id=str(conversation_id) if conversation_id else None
            # )
            #
            # if not selected_agent_info:
            selected_agent_info = task_analysis["sub_agents"][0]
            logger.info("未匹配到路由规则，使用默认 Agent")

            agent_id = selected_agent_info["agent_id"]

        # 3. 获取 Agent 配置
        agent_data = self.sub_agents.get(agent_id)
        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)

        agent_info = agent_data.get("info", {})

        # 4. 发送路由信息事件
        yield self._format_sse_event("agent_selected", {
            "agent_id": agent_id,
            "agent_name": agent_info.get("name"),
            "routing_decision": {
                "confidence": routing_decision.get("confidence") if routing_decision else None,
                "reasoning": routing_decision.get("reasoning") if routing_decision else None,
                "strategy": routing_decision.get("strategy") if routing_decision else None
            }
        })

        # 5. 流式执行子 Agent
        sub_conversation_id = None
        # Master Agent 路由决策消耗的 token，通过 sub_usage 事件发送给上层
        routing_tokens = task_analysis.get("routing_tokens", 0)
        if routing_tokens > 0:
            yield self._format_sse_event("sub_usage", {"total_tokens": routing_tokens})

        async for event in self._execute_sub_agent_stream(
            agent_data["config"],
            message,
            task_analysis.get("initial_context", {}),
            conversation_id,
            user_id,
            web_search,
            memory,
            storage_type,
            user_rag_memory_id
        ):
            # 解析事件以提取 conversation_id
            if "data:" in event:
                try:
                    import json
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    if "conversation_id" in data:
                        sub_conversation_id = data["conversation_id"]
                except:
                    pass

            # 子运行生命周期事件（start/end）不透传：否则上层会看到两个 start / 两个 end，
            # 且子运行的临时 message_id 会顶掉集群气泡的真实 id（详见 _SUB_RUN_LIFECYCLE_EVENTS）。
            if _sse_event_name(event) in _SUB_RUN_LIFECYCLE_EVENTS:
                continue

            # 其余事件直接透传（包括 sub_usage），累加统一由上层处理；
            # 顺手补上 Agent 归属（只补缺失字段，不覆盖事件自带同名字段），
            # 供运行中面板把事件分流到对应的子 Agent 区块。
            yield self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_info.get("name")))

        # 6. 如果有会话 ID，发送一个包含它的事件
        if sub_conversation_id:
            yield self._format_sse_event("conversation", {
                "conversation_id": sub_conversation_id
            })

    async def _execute_conditional(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> Dict[str, Any]:
        """条件路由执行（重构版 - 使用 Master Agent 的决策）

        Args:
            task_analysis: 任务分析结果（包含 Master Agent 的决策）
            conversation_id: 会话 ID
            user_id: 用户 ID

        Returns:
            执行结果
        """
        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

        message = task_analysis.get("message", "")
        routing_decision = task_analysis.get("routing_decision")

        if not routing_decision:
            raise BusinessException("缺少路由决策", BizCode.AGENT_CONFIG_MISSING)

        agent_id = routing_decision["selected_agent_id"]

        logger.info(
            "执行 Master Agent 的路由决策",
            extra={
                "agent_id": agent_id,
                "confidence": routing_decision.get("confidence"),
                "reasoning": routing_decision.get("reasoning")
            }
        )

        # 检查是否需要协作
        if routing_decision.get("need_collaboration"):
            collaboration_strategy = routing_decision.get("collaboration_strategy", "sequential")

            # 根据策略获取协作信息
            if collaboration_strategy == "decomposition":
                # 问题拆分模式：使用 sub_questions
                collaboration_agents = routing_decision.get("sub_questions", [])
                logger.info(
                    "Master Agent 建议问题拆分",
                    extra={
                        "sub_question_count": len(collaboration_agents),
                        "strategy": collaboration_strategy
                    }
                )
            else:
                # 其他协作模式：使用 collaboration_agents
                collaboration_agents = routing_decision.get("collaboration_agents", [])
                logger.info(
                    "Master Agent 建议多 Agent 协作",
                    extra={
                        "collaboration_agent_count": len(collaboration_agents),
                        "strategy": collaboration_strategy
                    }
                )

            # 执行多 Agent 协作
            return await self._execute_collaboration(
                message=message,
                collaboration_agents=collaboration_agents,
                strategy=collaboration_strategy,
                initial_context=task_analysis.get("initial_context", {}),
                conversation_id=conversation_id,
                user_id=user_id,
                routing_decision=routing_decision
            )

        # 3. 获取 Agent 配置
        agent_data = self.sub_agents.get(agent_id)
        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)

        agent_info = agent_data.get("info", {})

        logger.info(
            "执行选中的 Agent",
            extra={
                "agent_id": agent_id,
                "agent_name": agent_info.get("name"),
                "message_preview": message[:50]
            }
        )

        # 4. 执行 Agent
        result = await self._execute_sub_agent(
            agent_data["config"],
            message,
            task_analysis.get("initial_context", {}),
            conversation_id,
            user_id,
            web_search,
            memory,
            storage_type,
            user_rag_memory_id
        )

        # 5. 返回结果
        return {
            "agent_id": agent_id,
            "agent_name": agent_info.get("name"),
            "result": result,
            "conversation_id": result.get("conversation_id"),
            "routing_decision": routing_decision  # 包含 Master Agent 的决策信息
        }

    async def _execute_loop(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> Dict[str, Any]:
        """循环执行（迭代优化）

        Args:
            task_analysis: 任务分析结果
            conversation_id: 会话 ID
            user_id: 用户 ID

        Returns:
            执行结果
        """
        max_iterations = self.config.execution_config.get("max_iterations", 5)

        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

        agent_info = task_analysis["sub_agents"][0]
        agent_id = agent_info["agent_id"]
        agent_data = self.sub_agents.get(agent_id)

        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)

        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")

        result = None
        for i in range(max_iterations):
            logger.info(
                "循环执行 Agent",
                extra={
                    "iteration": i + 1,
                    "max_iterations": max_iterations,
                    "agent_name": agent_info.get("name")
                }
            )

            result = await self._execute_sub_agent(
                agent_data["config"],
                message,
                context,
                conversation_id,
                user_id,
                web_search,
                memory,
                storage_type,
                user_rag_memory_id
            )

            # 简化版本：执行一次就返回
            # 在实际应用中，应该验证结果是否满足条件
            break

        return {
            "agent_id": agent_id,
            "agent_name": agent_info.get("name"),
            "iterations": i + 1,
            "result": result,
            "conversation_id": result.get("conversation_id") if result else None  # 保存会话 ID
        }

    def _resolve_agent_identity(self, agent_config) -> Dict[str, Any]:
        """由 AgentConfigProxy 反查 sub_agents 里的 agent_id，并给出落库用的 owner 信息。

        子 Agent 的 agent_config 是 AgentConfigProxy（_load_agent_async 构造），
        它的 id 就是 app_releases.id —— 不能写进 agent_config_id（FK→agent_configs.id），
        所以统一走 release_id；agent_name 用于 meta_data.agent_name 与详情页 node_name。
        """
        release_id = getattr(agent_config, "id", None)
        agent_name = getattr(agent_config, "name", None)
        agent_id = None
        for key, data in (self.sub_agents or {}).items():
            cfg = data.get("config")
            if cfg is agent_config or (
                cfg is not None and str(getattr(cfg, "id", "")) == str(release_id or "")
            ):
                agent_id = key
                break
        if not agent_name and agent_id:
            agent_name = (self.sub_agents.get(agent_id, {}).get("info", {}) or {}).get("name") or agent_id
        return {"release_id": release_id, "agent_name": agent_name, "agent_id": agent_id}

    async def _execute_sub_agent_stream(
        self,
        agent_config: AgentConfig,
        message: str,
        context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ):
        """执行单个子 Agent（流式）

        Args:
            agent_config: Agent 配置
            message: 消息
            context: 上下文
            conversation_id: 会话 ID
            user_id: 用户 ID

        Yields:
            SSE 格式的事件流
        """
        from app.services.draft_run_service import AgentRunService

        # 获取模型配置
        model_config = await self._db_get(ModelConfig, agent_config.default_model_config_id)
        if not model_config:
            raise BusinessException(
                "Agent 模型配置不存在",
                BizCode.AGENT_CONFIG_MISSING
            )

        # 流式执行 Agent
        # 归属信息由实例属性（current_execution_id）+ 本地反查得到，
        # 而不在 10 个调用点逐个加参数 —— 加参数必然漏改（流式路径曾因此炸外键）。
        draft_service = AgentRunService(self.db)
        async for event in draft_service.run_stream(
            agent_config=agent_config,
            model_config=model_config,
            message=message,
            workspace_id=agent_config.app.workspace_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_id=user_id,
            variables=context,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            web_search=web_search,
            memory=memory,
            sub_agent=True,
            parent_execution_id=self.current_execution_id,
            orchestration_mode=self._normalized_mode,
            execution_owner=self._resolve_agent_identity(agent_config),
        ):
            yield event

    async def _execute_sub_agent(
        self,
        agent_config: AgentConfig,
        message: str,
        context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ) -> Dict[str, Any]:
        """执行单个子 Agent

        Args:
            agent_config: Agent 配置
            message: 消息
            context: 上下文
            conversation_id: 会话 ID
            user_id: 用户 ID


        Returns:
            执行结果
        """
        from app.services.draft_run_service import AgentRunService

        # 获取模型配置
        model_config = await self._db_get(ModelConfig, agent_config.default_model_config_id)
        if not model_config:
            raise BusinessException(
                "Agent 模型配置不存在",
                BizCode.AGENT_CONFIG_MISSING
            )

        # 执行 Agent
        draft_service = AgentRunService(self.db)
        result = await draft_service.run(
            agent_config=agent_config,
            model_config=model_config,
            message=message,
            workspace_id=agent_config.app.workspace_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_id=user_id,
            variables=context,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            sub_agent=True,
            parent_execution_id=self.current_execution_id,
            orchestration_mode=self._normalized_mode,
            execution_owner=self._resolve_agent_identity(agent_config),
        )

        return result

    async def _aggregate_results(
        self,
        results: Any
    ) -> str:
        """整合子 Agent 的结果

        Args:
            results: 子 Agent 执行结果

        Returns:
            整合后的结果
        """
        strategy = self.config.aggregation_strategy

        if strategy == AggregationStrategy.MERGE:
            return self._merge_results(results)
        elif strategy == AggregationStrategy.VOTE:
            return self._vote_results(results)
        elif strategy == AggregationStrategy.PRIORITY:
            return self._priority_results(results)
        else:
            return self._merge_results(results)

    def _merge_results(self, results: Any) -> str:
        """合并所有结果

        Args:
            results: 执行结果

        Returns:
            合并后的结果
        """
        if isinstance(results, list):
            # 顺序或并行执行的结果
            merged = []
            for item in results:
                if "result" in item:
                    agent_name = item.get("agent_name", "Agent")
                    message = item["result"].get("message", "")
                    merged.append(f"【{agent_name}】\n{message}")
                elif "error" in item:
                    agent_name = item.get("agent_name", "Agent")
                    merged.append(f"【{agent_name}】\n错误: {item['error']}")

            return "\n\n".join(merged)
        elif isinstance(results, dict):
            # 条件或循环执行的结果
            if "result" in results:
                return results["result"].get("message", "")
            return str(results)

        return str(results)

    def _vote_results(self, results: Any) -> str:
        """投票选择最佳结果（简化版本）

        Args:
            results: 执行结果

        Returns:
            最佳结果
        """
        # 简化版本：返回第一个成功的结果
        if isinstance(results, list):
            for item in results:
                if "result" in item:
                    return item["result"].get("message", "")

        return self._merge_results(results)

    def _priority_results(self, results: Any) -> str:
        """按优先级选择结果（简化版本）

        Args:
            results: 执行结果

        Returns:
            优先级最高的结果
        """
        # 简化版本：返回第一个结果
        if isinstance(results, list) and results:
            if "result" in results[0]:
                return results[0]["result"].get("message", "")

        return self._merge_results(results)

    async def _execute_collaboration_mode_stream(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = ''
    ):
        """Collaboration 模式流式执行 - Agent 之间可以相互 handoff

        使用 handoffs_service 实现 Agent 之间的动态切换

        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            web_search: 是否启用网络搜索
            memory: 是否启用记忆
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆 ID

        Yields:
            SSE 格式的事件流
        """
        from app.services.handoffs_service import (
            convert_multi_agent_config_to_handoffs,
            HandoffsService
        )

        try:
            # 1. 构建 multi_agent_config 字典
            multi_agent_config = {
                "sub_agents": self.config.sub_agents,
                "orchestration_mode": self.config.orchestration_mode
            }

            # 2. 转换配置（每个 Agent 包含自己的 model_config）
            agent_configs = await convert_multi_agent_config_to_handoffs(
                multi_agent_config,
                self.db
            )

            if not agent_configs:
                raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

            # 3. 创建 HandoffsService
            handoffs_service = HandoffsService(
                agent_configs=agent_configs,
                streaming=True
            )

            # 4. 使用 handoffs_service 的流式聊天
            conv_id = str(conversation_id) if conversation_id else None

            # 协作模式不经过 AgentRunService，子 Agent 记录按"节点激活"补：
            # 收到 agent 事件开一条 sub 记录，下一个 agent / end / error 到达时收尾上一条。
            # 同一 Agent 多次激活 → 多行、execution_id 各不相同。
            active_execution_id = None
            active_agent_key = None
            active_started_at = 0.0
            active_tokens = 0
            # 协作模式没有 AgentTraceRecorder 的 trace，按激活粒度手工累积"调用链明细"：
            # 该 Agent 收到的任务 / 流式产出的文本 / 它发起的 handoff。
            active_task = message
            active_text = ""
            active_steps: list = []
            pending_task = None

            def _activation_steps(status: str, agent_name: Optional[str]) -> list:
                """把一次激活折叠成 steps：首项是它自己的输入/产出，其后是它发起的 handoff。"""
                head = {
                    "step_id": f"collab_{active_execution_id}",
                    "node_type": "agent",
                    "node_name": agent_name or active_agent_key,
                    "status": status,
                    "input": active_task,
                    "output": active_text,
                }
                return [head, *active_steps]

            async for event in handoffs_service.chat_stream(
                message=message,
                conversation_id=conv_id
            ):
                event_name, event_data = self._parse_sse_event(event)

                # Agent 切换：先收尾上一条激活，再开新的
                if event_name == "agent":
                    if active_execution_id is not None:
                        closing_info = agent_configs.get(active_agent_key or "", {}) or {}
                        yield self._format_sse_event("agent_complete", {
                            "execution_id": str(active_execution_id),
                            "agent_id": closing_info.get("agent_id"),
                            "agent_name": closing_info.get("name"),
                            "status": "completed",
                            "output": active_text,
                            "elapsed_time": time.time() - active_started_at,
                            "token_usage": {"total_tokens": active_tokens},
                        })
                        await self._close_collaboration_activation(
                            active_execution_id,
                            "completed",
                            time.time() - active_started_at,
                            token_usage={"total_tokens": active_tokens},
                            steps=_activation_steps("completed", closing_info.get("name")),
                        )
                        active_execution_id = None

                    active_agent_key = event_data.get("agent")
                    active_started_at = time.time()
                    active_tokens = 0
                    active_text = ""
                    active_steps = []
                    # 被 handoff 转过来的任务：优先用上一条激活写下的 unhandled_question
                    active_task = pending_task or message
                    pending_task = None
                    active_execution_id, meta = await self._open_collaboration_activation(
                        agent_configs,
                        active_agent_key,
                        event_data.get("agent_name"),
                        active_task,
                    )
                    yield self._format_sse_event("agent_dispatch", meta)
                    yield self._inject_agent_meta(event, meta)
                    continue

                if event_name == "sub_usage":
                    active_tokens += int(event_data.get("total_tokens") or 0)
                elif event_name == "message":
                    active_text += event_data.get("content") or ""
                elif event_name == "handoff":
                    # handoff 归属"发起方"这次激活：它转出给谁、为什么转、待解决什么问题
                    active_steps.append({
                        "step_id": f"handoff_{active_execution_id}_{len(active_steps)}",
                        "node_type": "handoff",
                        "node_name": f"→ {event_data.get('to_name') or event_data.get('to')}",
                        "status": "completed",
                        "input": {
                            "from": event_data.get("from"),
                            "reason": event_data.get("reason"),
                            "your_answer": event_data.get("your_answer"),
                        },
                        "output": {"unhandled_question": event_data.get("unhandled_question")},
                    })
                    pending_task = event_data.get("unhandled_question") or active_task

                # 归属注入：只补 data 里缺失的字段，事件名与其它字段原样保留
                if active_execution_id is not None:
                    active_info = agent_configs.get(active_agent_key or "", {}) or {}
                    event = self._inject_agent_meta(event, {
                        "execution_id": str(active_execution_id),
                        "agent_id": active_info.get("agent_id") or active_agent_key,
                        "agent_name": active_info.get("name") or active_agent_key,
                        "parent_execution_id": str(self.current_execution_id) if self.current_execution_id else None,
                        "orchestration_mode": self._normalized_mode,
                    })
                elif event_name == "end":
                    # 整轮结束却没有激活记录（降级场景）：只补编排模式，便于前端识别
                    event = self._inject_agent_meta(event, {
                        "orchestration_mode": self._normalized_mode,
                    })

                # handoffs 自己的 end 属于"协作运行生命周期"，不是集群会话事件：
                # 集群级 end 由 execute_stream 在模式分支之后统一发出，这里不透传，
                # 否则上层会看到两个 end（且第二个 end 已经把外层消息收过尾了）。
                if event_name != "end":
                    yield event

                if event_name in ("end", "error") and active_execution_id is not None:
                    final_status = "completed" if event_name == "end" else "failed"
                    final_info = agent_configs.get(active_agent_key or "", {}) or {}
                    yield self._format_sse_event("agent_complete", {
                        "execution_id": str(active_execution_id),
                        "agent_id": final_info.get("agent_id"),
                        "agent_name": final_info.get("name"),
                        "status": final_status,
                        "output": active_text,
                        "elapsed_time": time.time() - active_started_at,
                        "token_usage": {"total_tokens": active_tokens},
                    })
                    await self._close_collaboration_activation(
                        active_execution_id,
                        final_status,
                        time.time() - active_started_at,
                        token_usage={"total_tokens": active_tokens},
                        error_message=(str(event_data.get("error"))[:2000] if event_name == "error" else None),
                        steps=_activation_steps(final_status, final_info.get("name")),
                    )
                    active_execution_id = None

        except Exception as e:
            logger.error(f"Collaboration 模式执行失败: {str(e)}", exc_info=True)
            # 收尾可能仍处于"激活中"的子 Agent 记录，避免状态永远停在 running
            # （异常若发生在激活变量定义之前，NameError 一并被吞掉）
            try:
                if active_execution_id is not None:
                    yield self._format_sse_event("agent_complete", {
                        "execution_id": str(active_execution_id),
                        "agent_name": (agent_configs.get(active_agent_key or "", {}) or {}).get("name"),
                        "status": "failed",
                        "output": active_text,
                        "elapsed_time": time.time() - active_started_at,
                        "token_usage": {"total_tokens": active_tokens},
                    })
                    await self._close_collaboration_activation(
                        active_execution_id,
                        "failed",
                        time.time() - active_started_at,
                        token_usage={"total_tokens": active_tokens},
                        error_message=str(e)[:2000],
                    )
            except Exception:
                pass
            yield self._format_sse_event("error", {
                "error": str(e),
                "timestamp": time.time()
            })

    async def _execute_collaboration_mode(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Collaboration 模式非流式执行 - Agent 之间可以相互 handoff

        使用 handoffs_service 实现 Agent 之间的动态切换

        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数

        Returns:
            执行结果
        """
        from app.services.handoffs_service import (
            convert_multi_agent_config_to_handoffs,
            HandoffsService
        )

        start_time = time.time()

        try:
            # 1. 构建 multi_agent_config 字典
            multi_agent_config = {
                "sub_agents": self.config.sub_agents,
                "orchestration_mode": self.config.orchestration_mode
            }

            # 2. 转换配置（每个 Agent 包含自己的 model_config）
            agent_configs = await convert_multi_agent_config_to_handoffs(
                multi_agent_config,
                self.db
            )

            if not agent_configs:
                raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

            # 3. 创建 HandoffsService
            handoffs_service = HandoffsService(
                agent_configs=agent_configs,
                streaming=False
            )

            # 4. 使用 handoffs_service 的非流式聊天
            conv_id = str(conversation_id) if conversation_id else None

            result = await handoffs_service.chat(
                message=message,
                conversation_id=conv_id
            )

            elapsed_time = time.time() - start_time

            return {
                "message": result.get("response", ""),
                "conversation_id": result.get("conversation_id"),
                "mode": OrchestrationMode.COLLABORATION,
                "elapsed_time": elapsed_time,
                "strategy": "collaboration",
                "active_agent": result.get("active_agent"),
                "sub_results": result,
                "usage": result.get("usage")
            }

        except Exception as e:
            logger.error(f"Collaboration 模式执行失败: {str(e)}", exc_info=True)
            raise

    def _format_sse_event(self, event: str, data: Dict[str, Any]) -> str:
        """格式化 SSE 事件

        Args:
            event: 事件类型
            data: 事件数据

        Returns:
            SSE 格式的字符串
        """
        import json
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def _load_agent_async(self, release_id: uuid.UUID):
        """从发布版本加载 Agent 配置

        Args:
            release_id: 发布版本 ID

        Returns:
            Agent 配置对象（包含发布版本的配置数据）
        """
        from app.models import AppRelease, App

        # 获取发布版本
        release = await self._db_get(AppRelease, release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(release_id))

        # 从发布版本的 config 中获取 Agent 配置
        config_data = release.config
        if not config_data:
            raise BusinessException(f"发布版本 {release_id} 缺少配置数据", BizCode.AGENT_CONFIG_MISSING)

        # 获取应用信息（用于 workspace_id）
        app = await self._db_get(App, release.app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(release.app_id))

        # 创建一个类似 AgentConfig 的对象，包含所有需要的属性
        class AgentConfigProxy:
            """Agent 配置代理对象，模拟 AgentConfig 的接口"""
            def __init__(self, release, app, config_data):
                self.id = release.id
                self.app_id = release.app_id
                self.app = app
                self.name = release.name
                self.description = release.description
                self.system_prompt = config_data.get("system_prompt")
                self.model_parameters = config_data.get("model_parameters")
                self.knowledge_retrieval = config_data.get("knowledge_retrieval")
                self.memory = config_data.get("memory")
                self.variables = config_data.get("variables", [])
                self.tools = config_data.get("tools", {})
                self.skills = config_data.get("skills", {})
                self.features = config_data.get("features", {})
                self.default_model_config_id = release.default_model_config_id

        return AgentConfigProxy(release, app, config_data)

    async def _execute_collaboration(
        self,
        message: str,
        collaboration_agents: List[Dict[str, Any]],
        strategy: str,
        initial_context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行多 Agent 协作

        Args:
            message: 用户消息
            collaboration_agents: 协作 Agent 列表
            strategy: 协作策略（sequential/parallel/hierarchical）
            initial_context: 初始上下文
            conversation_id: 会话 ID
            user_id: 用户 ID
            routing_decision: 路由决策

        Returns:
            协作执行结果
        """
        logger.info(
            "开始多 Agent 协作",
            extra={
                "agent_count": len(collaboration_agents),
                "strategy": strategy
            }
        )

        if strategy == "decomposition":
            # 问题拆分：每个 Agent 处理一个子问题
            return await self._execute_decomposition_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        elif strategy == "sequential":
            # 顺序协作：按顺序执行，后续 Agent 可以使用前面的结果
            return await self._execute_sequential_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        elif strategy == "parallel":
            # 并行协作：同时执行所有 Agent
            return await self._execute_parallel_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        elif strategy == "hierarchical":
            # 层级协作：主 Agent 协调，其他 Agent 辅助
            return await self._execute_hierarchical_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        else:
            # 默认使用顺序协作
            return await self._execute_sequential_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )

    def _check_dependencies(self, sub_questions: List[Dict[str, Any]]) -> bool:
        """检测子问题是否有依赖关系

        Args:
            sub_questions: 子问题列表

        Returns:
            True 如果有依赖关系，False 如果完全独立
        """
        for sub_q in sub_questions:
            depends_on = sub_q.get("depends_on", [])
            if depends_on and len(depends_on) > 0:
                logger.info(
                    "检测到依赖关系",
                    extra={
                        "question": sub_q.get("question", "")[:50],
                        "depends_on": depends_on
                    }
                )
                return True
        return False

    async def _execute_decomposition_collaboration(
        self,
        message: str,
        collaboration_agents: List[Dict[str, Any]],
        initial_context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """问题拆分执行

        每个 Agent 处理一个独立的子问题，避免重复

        示例：
        原问题："写一首关于雪的古诗，并计算3+8"
        拆分后：
        - 子问题1："写一首关于雪的古诗" → 文科导师
        - 子问题2："计算3+8" → 理科导师

        Args:
            collaboration_agents: 在 decomposition 模式下，这就是 sub_questions 列表
        """
        results = []

        # collaboration_agents 在 decomposition 模式下就是 sub_questions
        sub_questions = collaboration_agents

        if not sub_questions:
            # 如果没有子问题，降级到普通协作
            logger.warning(
                "问题拆分模式但没有子问题，降级到顺序协作",
                extra={
                    "collaboration_agents": collaboration_agents,
                    "routing_decision": routing_decision
                }
            )
            return await self._execute_sequential_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )

        logger.info(
            "开始问题拆分执行",
            extra={
                "sub_question_count": len(sub_questions),
                "original_message": message[:50]
            }
        )

        # 检测是否有依赖关系
        has_dependencies = self._check_dependencies(sub_questions)

        # 获取执行模式配置
        execution_mode = self.config.execution_config.get("sub_agent_execution_mode", "parallel")

        # 如果有依赖关系，强制使用串行模式
        if has_dependencies:
            logger.info("检测到子问题有依赖关系，强制使用串行执行")
            execution_mode = "sequential"

        if execution_mode == "sequential":
            # 串行执行模式
            logger.info(f"串行执行 {len(sub_questions)} 个子问题")

            # 用于存储已完成的子问题结果（按 order 索引）
            completed_results = {}

            for sub_q in sorted(sub_questions, key=lambda x: x.get("order", 0)):
                sub_question = sub_q.get("question", "")
                agent_id = sub_q.get("agent_id")
                order = sub_q.get("order", 0)
                depends_on = sub_q.get("depends_on", [])

                agent_data = self.sub_agents.get(agent_id)
                if not agent_data:
                    logger.warning(
                        f"子问题对应的 Agent 不存在: {agent_id}",
                        extra={
                            "sub_question": sub_question,
                            "available_agents": list(self.sub_agents.keys())
                        }
                    )
                    continue

                agent_name = agent_data.get("info", {}).get("name", agent_id)

                # 如果有依赖，构建包含依赖结果的上下文
                context_with_deps = initial_context.copy()
                if depends_on:
                    dependency_results = []
                    for dep_order in depends_on:
                        if dep_order in completed_results:
                            dep_result = completed_results[dep_order]
                            dependency_results.append({
                                "question": dep_result.get("sub_question"),
                                "answer": dep_result.get("result", {}).get("message", "")
                            })

                    if dependency_results:
                        context_with_deps["previous_results"] = dependency_results
                        logger.info(
                            "子问题依赖前置结果",
                            extra={
                                "current_order": order,
                                "depends_on": depends_on,
                                "dependency_count": len(dependency_results)
                            }
                        )

                logger.info(
                    "处理子问题（串行）",
                    extra={
                        "sub_question": sub_question,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "has_dependencies": bool(depends_on)
                    }
                )

                # 串行执行
                try:
                    result = await self._execute_sub_agent(
                        agent_data["config"],
                        sub_question,
                        context_with_deps,  # 使用包含依赖结果的上下文
                        conversation_id,
                        user_id
                    )
                    result_entry = {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "sub_question": sub_question,
                        "result": result,
                        "conversation_id": result.get("conversation_id"),
                        "order": order
                    }
                    results.append(result_entry)
                    completed_results[order] = result_entry  # 保存结果供后续依赖使用
                except Exception as e:
                    logger.error(f"子问题执行失败: {str(e)}")
                    results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "sub_question": sub_question,
                        "error": str(e),
                        "order": order
                    })
        else:
            # 并行执行模式（默认）
            tasks = []
            agent_infos = []

            for sub_q in sorted(sub_questions, key=lambda x: x.get("order", 0)):
                sub_question = sub_q.get("question", "")
                agent_id = sub_q.get("agent_id")

                agent_data = self.sub_agents.get(agent_id)
                if not agent_data:
                    logger.warning(f"子问题对应的 Agent 不存在: {agent_id}")
                    continue

                agent_name = agent_data.get("info", {}).get("name", agent_id)

                logger.info(
                    "准备处理子问题（并行）",
                    extra={
                        "sub_question": sub_question,
                        "agent_id": agent_id,
                        "agent_name": agent_name
                    }
                )

                # 创建异步任务
                task = self._execute_sub_agent(
                    agent_data["config"],
                    sub_question,
                    initial_context,
                    conversation_id,
                    user_id
                )
                tasks.append(task)
                agent_infos.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "sub_question": sub_question
                })

            # 并行执行所有任务
            logger.info(f"并行执行 {len(tasks)} 个子问题")
            task_results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            for i, result in enumerate(task_results):
                if isinstance(result, Exception):
                    logger.error(f"子问题执行失败: {str(result)}")
                    results.append({
                        "agent_id": agent_infos[i]["agent_id"],
                        "agent_name": agent_infos[i]["agent_name"],
                        "sub_question": agent_infos[i]["sub_question"],
                        "error": str(result)
                    })
                else:
                    results.append({
                        "agent_id": agent_infos[i]["agent_id"],
                        "agent_name": agent_infos[i]["agent_name"],
                        "sub_question": agent_infos[i]["sub_question"],
                        "result": result,
                        "conversation_id": result.get("conversation_id")
                    })

        # 整合结果（问题拆分模式）
        final_response = await self._merge_decomposition_results(results, message)

        return {
            "agent_id": "decomposition",
            "agent_name": "问题拆分协作",
            "result": {
                "message": final_response,
                "conversation_id": results[0].get("conversation_id") if results else None
            },
            "conversation_id": results[0].get("conversation_id") if results else None,
            "routing_decision": routing_decision,
            "collaboration_results": results
        }

    async def _execute_sequential_collaboration(
        self,
        message: str,
        collaboration_agents: List[Dict[str, Any]],
        initial_context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """顺序协作执行

        每个 Agent 按顺序执行，后续 Agent 可以看到前面 Agent 的结果
        """
        results = []
        context = initial_context.copy()
        accumulated_response = []

        # 按 order 排序
        sorted_agents = sorted(collaboration_agents, key=lambda x: x.get("order", 0))

        for agent_info in sorted_agents:
            agent_id = agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)

            if not agent_data:
                logger.warning(f"协作 Agent 不存在: {agent_id}")
                continue

            agent_name = agent_data.get("info", {}).get("name", agent_id)
            agent_task = agent_info.get("task", "处理任务")

            logger.info(
                "执行协作 Agent",
                extra={
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "role": agent_info.get("role"),
                    "task": agent_task,
                    "order": agent_info.get("order")
                }
            )

            # 构建该 Agent 的消息（包含任务说明和前面的结果）
            agent_message = message
            if context.get("previous_results"):
                agent_message = f"""原始问题：{message}

你的任务：{agent_task}

前面专家的分析结果：
{context['previous_results']}

请基于以上信息，完成你的任务。"""

            # 执行 Agent
            result = await self._execute_sub_agent(
                agent_data["config"],
                agent_message,
                context,
                conversation_id,
                user_id
            )

            agent_response = result.get("message", "")

            results.append({
                "agent_id": agent_id,
                "agent_name": agent_name,
                "role": agent_info.get("role"),
                "task": agent_task,
                "result": result,
                "conversation_id": result.get("conversation_id")
            })

            # 更新上下文
            context[f"result_from_{agent_name}"] = agent_response

            # 累积响应
            accumulated_response.append(f"【{agent_name}】\n{agent_response}")

            # 更新 previous_results 供下一个 Agent 使用
            context["previous_results"] = "\n\n".join(accumulated_response)

        # 整合最终结果
        final_response = await self._merge_collaboration_results(
            results,
            strategy="sequential",
            original_question=message
        )

        return {
            "agent_id": "collaboration",
            "agent_name": "多Agent协作",
            "result": {
                "message": final_response,
                "conversation_id": results[0].get("conversation_id") if results else None
            },
            "conversation_id": results[0].get("conversation_id") if results else None,
            "routing_decision": routing_decision,
            "collaboration_results": results
        }

    async def _execute_parallel_collaboration(
        self,
        message: str,
        collaboration_agents: List[Dict[str, Any]],
        initial_context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """并行协作执行

        所有 Agent 同时执行，互不依赖
        """
        tasks = []
        agent_infos = []

        for agent_info in collaboration_agents:
            agent_id = agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)

            if not agent_data:
                continue

            agent_task = agent_info.get("task", "处理任务")

            # 构建该 Agent 的消息
            agent_message = f"""原始问题：{message}

你的任务：{agent_task}

请完成你的任务。"""

            # 创建任务
            task = self._execute_sub_agent(
                agent_data["config"],
                agent_message,
                initial_context.copy(),
                conversation_id,
                user_id
            )
            tasks.append(task)
            agent_infos.append((agent_id, agent_data, agent_info))

        # 并行执行
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        results = []
        for (agent_id, agent_data, agent_info), result in zip(agent_infos, task_results, strict=False):
            agent_name = agent_data.get("info", {}).get("name", agent_id)

            if isinstance(result, Exception):
                logger.error(f"协作 Agent 执行失败: {agent_name}", extra={"error": str(result)})
                results.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "error": str(result)
                })
            else:
                results.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "role": agent_info.get("role"),
                    "task": agent_info.get("task"),
                    "result": result,
                    "conversation_id": result.get("conversation_id")
                })

        # 整合结果
        final_response = await self._merge_collaboration_results(
            results,
            strategy="parallel",
            original_question=message
        )

        return {
            "agent_id": "collaboration",
            "agent_name": "多Agent协作",
            "result": {
                "message": final_response,
                "conversation_id": results[0].get("conversation_id") if results else None
            },
            "conversation_id": results[0].get("conversation_id") if results else None,
            "routing_decision": routing_decision,
            "collaboration_results": results
        }

    async def _execute_hierarchical_collaboration(
        self,
        message: str,
        collaboration_agents: List[Dict[str, Any]],
        initial_context: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """层级协作执行

        主 Agent（primary）负责协调，其他 Agent 提供辅助信息
        """
        # 找到主 Agent 和辅助 Agents
        primary_agent = None
        secondary_agents = []

        for agent_info in collaboration_agents:
            if agent_info.get("role") == "primary":
                primary_agent = agent_info
            else:
                secondary_agents.append(agent_info)

        if not primary_agent:
            # 如果没有指定主 Agent，使用第一个
            primary_agent = collaboration_agents[0]
            secondary_agents = collaboration_agents[1:]

        # 1. 先执行辅助 Agents（并行）
        secondary_results = []
        if secondary_agents:
            tasks = []
            agent_infos = []

            for agent_info in secondary_agents:
                agent_id = agent_info["agent_id"]
                agent_data = self.sub_agents.get(agent_id)

                if not agent_data:
                    continue

                agent_task = agent_info.get("task", "提供专业意见")
                agent_message = f"""问题：{message}

请从你的专业角度提供意见：{agent_task}"""

                task = self._execute_sub_agent(
                    agent_data["config"],
                    agent_message,
                    initial_context.copy(),
                    conversation_id,
                    user_id
                )
                tasks.append(task)
                agent_infos.append((agent_id, agent_data, agent_info))

            # 并行执行辅助 Agents
            task_results = await asyncio.gather(*tasks, return_exceptions=True)

            for (agent_id, agent_data, agent_info), result in zip(agent_infos, task_results, strict=False):
                agent_name = agent_data.get("info", {}).get("name", agent_id)

                if not isinstance(result, Exception):
                    secondary_results.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "role": "secondary",
                        "result": result
                    })

        # 2. 执行主 Agent（整合辅助 Agents 的结果）
        primary_agent_id = primary_agent["agent_id"]
        primary_agent_data = self.sub_agents.get(primary_agent_id)

        if not primary_agent_data:
            raise BusinessException(f"主协作 Agent 不存在: {primary_agent_id}", BizCode.AGENT_CONFIG_MISSING)

        # 构建主 Agent 的消息（包含辅助 Agents 的结果）
        primary_message = f"""问题：{message}

你的任务：{primary_agent.get('task', '综合分析并给出最终答案')}
"""

        if secondary_results:
            expert_opinions = []
            for sec_result in secondary_results:
                expert_opinions.append(
                    f"【{sec_result['agent_name']}的意见】\n{sec_result['result'].get('message', '')}"
                )

            primary_message += f"""

其他专家的意见：
{chr(10).join(expert_opinions)}

请综合以上专家意见，给出你的最终答案。"""

        # 执行主 Agent
        primary_result = await self._execute_sub_agent(
            primary_agent_data["config"],
            primary_message,
            initial_context,
            conversation_id,
            user_id
        )

        primary_agent_name = primary_agent_data.get("info", {}).get("name", primary_agent_id)

        # 整合所有结果
        all_results = [*secondary_results, {"agent_id": primary_agent_id, "agent_name": primary_agent_name, "role": "primary", "result": primary_result, "conversation_id": primary_result.get("conversation_id")}]

        return {
            "agent_id": primary_agent_id,
            "agent_name": primary_agent_name,
            "result": primary_result,
            "conversation_id": primary_result.get("conversation_id"),
            "routing_decision": routing_decision,
            "collaboration_results": all_results
        }

    async def _merge_decomposition_results(
        self,
        results: List[Dict[str, Any]],
        original_question: str = None
    ) -> str:
        """整合问题拆分的结果

        每个 Agent 处理了不同的子问题，需要按顺序组合

        Args:
            results: 结果列表，每个包含 sub_question 和 result
            original_question: 原始用户问题

        Returns:
            整合后的响应
        """
        if not results:
            return "未获取到有效结果"

        # 获取整合模式
        merge_mode = self.config.execution_config.get("result_merge_mode", "smart")

        if merge_mode == "master":
            # 使用 Master Agent 整合
            return await self._master_merge_results(results, "decomposition", original_question)
        else:
            # smart 模式：直接组合答案
            parts = []
            for result in results:
                message = result.get("result", {}).get("message", "")
                if message:
                    parts.append(message)

            return "\n\n".join(parts)

    async def _merge_collaboration_results(
        self,
        results: List[Dict[str, Any]],
        strategy: str,
        original_question: str = None
    ) -> str:
        """整合协作结果（智能去重和合并）

        Args:
            results: 协作结果列表
            strategy: 协作策略
            original_question: 原始用户问题

        Returns:
            整合后的响应
        """
        if not results:
            logger.error(
                "协作结果为空",
                extra={
                    "strategy": strategy,
                    "has_original_question": bool(original_question)
                }
            )
            return "协作执行失败，没有可用结果"

        # 获取整合策略配置
        merge_mode = self.config.execution_config.get("result_merge_mode", "smart")

        if merge_mode == "master":
            # Master Agent 整合：让 Master Agent 结合原始问题和子 Agent 答案生成最终回复
            return await self._master_merge_results(results, strategy, original_question)
        else:
            # 默认使用智能整合
            return self._smart_merge_results(results, strategy)

    # smart 整合的分块大小（字符）。smart 不经过模型，没有天然 token 节奏，
    # 按块推送是为了让主气泡逐步增长，而不是整篇一次性出现。
    _SMART_MERGE_CHUNK_CHARS = 240

    @staticmethod
    def _chunk_text(text: str, max_chars: int):
        """把长文本切成块：段落优先，超长段落再按长度硬切。

        用于 smart（拼接）路径的流式输出。空文本不产出任何块。
        """
        if not text:
            return
        buf = ""
        for para in text.split("\n\n"):
            piece = para + "\n\n"
            if len(buf) + len(piece) <= max_chars:
                buf += piece
                continue
            if buf:
                yield buf
                buf = ""
            while len(piece) > max_chars:
                yield piece[:max_chars]
                piece = piece[max_chars:]
            buf = piece
        if buf:
            yield buf

    async def _smart_merge_results_stream(
        self,
        results: List[Dict[str, Any]],
        strategy: str
    ):
        """smart 拼接结果的流式版本（不调用模型）。

        作为兜底路径使用：Master 整合不可用（未配置模型 / 无可用 API Key /
        调用异常）或 `result_merge_mode="smart"` 时，把拼接结果按块逐条 yield，
        避免主气泡出现"一坨突然出现"的观感。

        Yields:
            SSE 格式的 message 事件
        """
        final_response = self._smart_merge_results(results, strategy)
        if not final_response:
            logger.warning("smart 整合结果为空，不输出 message 事件")
            return
        for chunk in self._chunk_text(final_response, self._SMART_MERGE_CHUNK_CHARS):
            yield self._format_sse_event("message", {"content": chunk})
            # 让出事件循环：确保每个块独立 flush，前端能逐块渲染
            await asyncio.sleep(0)

    def _smart_merge_results(
        self,
        results: List[Dict[str, Any]],
        strategy: str
    ) -> str:
        """智能整合结果（去重、提取关键信息）

        适用场景：多个 Agent 回答相似问题，需要去重和优化

        注意：在流式场景下，用户已经看到了所有 Agent 的输出，
        这个方法主要用于生成一个"整合后的版本"供后续使用（如保存到数据库）
        """
        if not results:
            return ""

        # 提取所有消息
        messages = []
        for result in results:
            if "error" in result:
                continue
            message = result.get("result", {}).get("message", "")
            if message:
                messages.append(message)

        if not messages:
            return ""

        if len(messages) == 1:
            # 只有一个结果，直接返回
            return messages[0]

        # 多个结果：根据策略智能整合
        if strategy == "decomposition":
            # 问题拆分：将所有子问题的答案合并
            # 按顺序组合各个 Agent 的回答
            merged_parts = []
            for result in results:
                if "error" in result:
                    continue
                agent_name = result.get("agent_name", "")
                sub_question = result.get("sub_question", "")
                message = result.get("result", {}).get("message", "")
                if message:
                    if sub_question:
                        merged_parts.append(f"**{sub_question}**\n{message}")
                    else:
                        merged_parts.append(message)

            if merged_parts:
                return "\n\n".join(merged_parts)
            return ""

        elif strategy == "sequential":
            # 顺序协作：返回最后一个 Agent 的结果（它包含了前面的信息）
            return self._merge_sequential_smart(results)

        elif strategy == "parallel":
            # 并行协作：检查是否需要去重
            return self._merge_parallel_smart(results)

        elif strategy == "hierarchical":
            # 层级协作：只返回主 Agent 的结果
            return self._merge_hierarchical_smart(results)

        else:
            # 默认：返回最完整的一个
            return max(messages, key=len)

    def _merge_sequential_smart(self, results: List[Dict[str, Any]]) -> str:
        """智能整合顺序协作结果

        顺序协作的特点：后续 Agent 会引用前面的结果
        策略：只保留最后一个 Agent 的完整回答（它已经包含了前面的信息）
        """
        if not results:
            return ""

        # 获取最后一个成功的结果
        for result in reversed(results):
            if "error" not in result:
                message = result.get("result", {}).get("message", "")
                if message:
                    return message

        return "未获取到有效结果"

    def _merge_parallel_smart(self, results: List[Dict[str, Any]]) -> str:
        """智能整合并行协作结果

        并行协作的特点：多个独立观点
        策略：
        1. 如果回答高度相似（重复），只保留一个
        2. 如果回答不同，合并所有观点（但不显示 Agent 名称）
        """
        messages = []
        for result in results:
            if "error" in result:
                continue
            message = result.get("result", {}).get("message", "")
            if message:
                messages.append(message)

        if not messages:
            return "未获取到有效结果"

        if len(messages) == 1:
            return messages[0]

        # 检查相似度
        similarity = self._calculate_similarity(messages)

        if similarity > 0.7:
            # 高度相似，只返回最长的一个
            return max(messages, key=len)
        else:
            # 不同观点，合并（不显示 Agent 名称）
            # 使用分隔符区分不同部分
            return "\n\n---\n\n".join(messages)

    def _merge_hierarchical_smart(self, results: List[Dict[str, Any]]) -> str:
        """智能整合层级协作结果

        层级协作的特点：主 Agent 已经综合了辅助 Agent 的意见
        策略：只返回主 Agent 的结果
        """
        # 找到主 Agent 的结果
        for result in results:
            if result.get("role") == "primary":
                message = result.get("result", {}).get("message", "")
                if message:
                    return message

        # 如果没有找到主 Agent，返回最后一个
        if results:
            last_result = results[-1]
            return last_result.get("result", {}).get("message", "")

        return "未获取到有效结果"

    def _resolve_merge_params(self) -> Dict[str, Any]:
        """Master 整合阶段的生成参数。

        - max_tokens：读 `execution_config.merge_max_tokens`（默认 8192）。
          历史上写死 2000，长报告的整合结果会被截断，而整合输出正是用户看到的最终答案。
        - temperature：跟随多 Agent 配置的 model_parameters，未配置时 0.7。
        """
        mp = self.model_parameters

        def _get(key: str, default: Any) -> Any:
            if mp is None:
                return default
            val = mp.get(key, default) if isinstance(mp, dict) else getattr(mp, key, default)
            return default if val is None else val

        try:
            max_tokens = int(self.config.execution_config.get("merge_max_tokens", 8192))
        except (TypeError, ValueError, AttributeError):
            max_tokens = 8192

        return {
            "temperature": _get("temperature", 0.7),
            "max_tokens": max_tokens,
        }

    async def _master_merge_results(
        self,
        results: List[Dict[str, Any]],
        strategy: str,
        original_question: str = None
    ) -> str:
        """使用 Master Agent 整合多个子 Agent 的结果

        Args:
            results: 子 Agent 的响应结果列表
            strategy: 协作策略
            original_question: 原始用户问题

        Returns:
            Master Agent 整合后的最终回复
        """
        if not results:
            return "没有收到任何 Agent 的响应"

        if len(results) == 1:
            # 只有一个结果，直接返回
            return results[0].get('result', {}).get('message', '')

        # 构建子 Agent 回答的汇总
        agent_responses = []
        for i, result in enumerate(results, 1):
            if "error" in result:
                continue

            agent_name = result.get('agent_name', f'Agent {i}')
            task = result.get('task', '')
            message = result.get('result', {}).get('message', '')

            if message:
                response_info = {
                    'agent_name': agent_name,
                    'task': task,
                    'response': message
                }
                agent_responses.append(response_info)

        if not agent_responses:
            return "未获取到有效结果"

        # 构建 Master Agent 的整合 prompt
        responses_text = ""
        for resp in agent_responses:
            agent_name = resp['agent_name']
            task = resp['task']
            response = resp['response']

            if task:
                responses_text += f"\n### {agent_name}（任务：{task}）的回答：\n{response}\n"
            else:
                responses_text += f"\n### {agent_name} 的回答：\n{response}\n"

        # 根据策略调整整合指令
        strategy_instructions = {
            "decomposition": "这些是针对不同子问题的回答，请将它们整合成一个完整、连贯的答案。",
            "sequential": "这些是按顺序协作的结果，后面的 Agent 可能依赖前面的结果，请整合成最终答案。",
            "parallel": "这些是从不同角度并行分析的结果，请综合这些观点给出全面的答案。",
            "hierarchical": "这些是层级协作的结果，请综合各方意见给出最终答案。"
        }

        strategy_instruction = strategy_instructions.get(strategy, "请整合这些回答，生成统一的最终答案。")

        question_context = f"\n**原始问题**：{original_question}\n" if original_question else ""

        merge_prompt = f"""你是一个智能助手，现在需要整合多个专业 Agent 的回答，生成一个统一、连贯、完整的最终答案。
{question_context}
**各个专业 Agent 的回答**：
{responses_text}

**整合要求**：
{strategy_instruction}

请注意：
1. 结合原始问题和各个 Agent 的专业回答
2. 去除重复内容，保留所有有价值的信息
3. 确保答案逻辑清晰、表达流畅
4. 如果不同 Agent 的观点有冲突，请合理说明
5. 直接给出整合后的答案，不要添加"根据以上回答"等元信息

请生成最终的整合答案："""

        try:
            # 调用 Master Agent 的 LLM 进行整合
            from app.core.models import RedBearLLM
            from app.core.models.base import RedBearModelConfig
            from app.models import ModelType

            # 获取 Master Agent 的模型配置
            default_model_config_id = self.config.default_model_config_id
            if not default_model_config_id:
                logger.warning("没有配置 Master Agent，使用简单整合")
                return self._smart_merge_results(results, strategy)

            # 获取 API Key 配置
            # api_key_config = self.db.query(ModelApiKey).join(
            #     ModelConfig, ModelApiKey.model_configs
            # ).filter(
            #     ModelConfig.id == default_model_config_id,
            #     ModelApiKey.is_active.is_(True)
            # ).first()
            # api_keys = ModelApiKeyRepository.get_by_model_config(self.db, default_model_config_id)
            # api_key_config = api_keys[0] if api_keys else None
            api_key_config = await ModelApiKeyService.get_available_api_key_bridge_async(
                self.db,
                default_model_config_id,
                tenant_id=self.tenant_id,
            )

            if not api_key_config:
                logger.warning("Master Agent 没有可用的 API Key，使用简单整合")
                return self._smart_merge_results(results, strategy)

            logger.info(
                "使用 Master Agent 整合结果",
                extra={
                    "agent_count": len(agent_responses),
                    "strategy": strategy,
                    "has_original_question": bool(original_question)
                }
            )

            # 创建 RedBearModelConfig
            _merge_params = self._resolve_merge_params()
            model_config = RedBearModelConfig.from_api_key(
                api_key_config,
                extra_params={
                    "temperature": _merge_params["temperature"],
                    "max_tokens": _merge_params["max_tokens"],
                },
            )

            # 创建 LLM 实例
            llm = RedBearLLM(model_config, type=ModelType.CHAT)

            # 调用模型进行整合
            response = await llm.ainvoke(merge_prompt)

            await ModelApiKeyService.record_api_key_usage_bridge_async(self.db, api_key_config.id)

            # 提取整合消耗的 token
            merge_tokens = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                um = response.usage_metadata
                merge_tokens = um.get("total_tokens", 0) if isinstance(um, dict) else getattr(um, "total_tokens", 0)
            elif hasattr(response, 'response_metadata') and response.response_metadata:
                token_usage = response.response_metadata.get("token_usage") or response.response_metadata.get("usage", {})
                if isinstance(token_usage, dict):
                    merge_tokens = token_usage.get("total_tokens", 0)
            self._last_merge_tokens = merge_tokens

            # 提取响应内容
            if hasattr(response, 'content'):
                merged_response = response.content
            else:
                merged_response = str(response)

            logger.info(
                "Master Agent 整合完成",
                extra={
                    "merged_length": len(merged_response),
                    "merge_tokens": merge_tokens
                }
            )

            return merged_response

        except Exception as e:
            logger.error(f"Master Agent 整合失败: {str(e)}")
            # 降级到智能整合
            return self._smart_merge_results(results, strategy)

    async def _master_merge_results_stream(
        self,
        results: List[Dict[str, Any]],
        strategy: str,
        original_question: str = None
    ):
        """使用 Master Agent 流式整合多个子 Agent 的结果

        Args:
            results: 子 Agent 的响应结果列表
            strategy: 协作策略
            original_question: 原始用户问题

        Yields:
            SSE 格式的事件流
        """
        if not results:
            yield self._format_sse_event("message", {"content": "没有收到任何 Agent 的响应"})
            return

        if len(results) == 1:
            # 只有一个结果，直接返回
            yield self._format_sse_event("message", {
                "content": results[0].get('result', {}).get('message', '')
            })
            return

        # 构建子 Agent 回答的汇总（与非流式版本相同）
        agent_responses = []
        for i, result in enumerate(results, 1):
            if "error" in result:
                continue

            agent_name = result.get('agent_name', f'Agent {i}')
            task = result.get('task', '')
            sub_question = result.get('sub_question', '')
            message = result.get('result', {}).get('message', '')

            if message:
                response_info = {
                    'agent_name': agent_name,
                    'task': task or sub_question,
                    'response': message
                }
                agent_responses.append(response_info)

        if not agent_responses:
            yield self._format_sse_event("message", {"content": "未获取到有效结果"})
            return

        # 构建整合 prompt
        responses_text = ""
        for resp in agent_responses:
            agent_name = resp['agent_name']
            task = resp['task']
            response = resp['response']

            if task:
                responses_text += f"\n### {agent_name}（任务：{task}）的回答：\n{response}\n"
            else:
                responses_text += f"\n### {agent_name} 的回答：\n{response}\n"

        strategy_instructions = {
            "decomposition": "这些是针对不同子问题的回答，请将它们整合成一个完整、连贯的答案。",
            "sequential": "这些是按顺序协作的结果，后面的 Agent 可能依赖前面的结果，请整合成最终答案。",
            "parallel": "这些是从不同角度并行分析的结果，请综合这些观点给出全面的答案。",
            "hierarchical": "这些是层级协作的结果，请综合各方意见给出最终答案。"
        }

        strategy_instruction = strategy_instructions.get(strategy, "请整合这些回答，生成统一的最终答案。")
        question_context = f"\n**原始问题**：{original_question}\n" if original_question else ""

        merge_prompt = f"""你是一个智能助手，现在需要整合多个专业 Agent 的回答，生成一个统一、连贯、完整的最终答案。
{question_context}
**各个专业 Agent 的回答**：
{responses_text}

**整合要求**：
{strategy_instruction}

请注意：
1. 结合原始问题和各个 Agent 的专业回答
2. 去除重复内容，保留所有有价值的信息
3. 确保答案逻辑清晰、表达流畅
4. 如果不同 Agent 的观点有冲突，请合理说明
5. 直接给出整合后的答案，不要添加"根据以上回答"等元信息

请生成最终的整合答案："""

        try:
            from app.core.models import RedBearLLM
            from app.core.models.base import RedBearModelConfig
            from app.models import ModelType

            # 获取 Master Agent 的模型配置
            default_model_config_id = self.config.default_model_config_id
            if not default_model_config_id:
                logger.warning("没有配置 Master Agent，降级为 smart 拼接（分块流式）")
                async for _evt in self._smart_merge_results_stream(results, strategy):
                    yield _evt
                return

            # 获取 API Key 配置
            # api_key_config = self.db.query(ModelApiKey).join(
            #     ModelConfig, ModelApiKey.model_configs
            # ).filter(
            #     ModelConfig.id == default_model_config_id,
            #     ModelApiKey.is_active.is_(True)
            # ).first()
            # api_keys = ModelApiKeyRepository.get_by_model_config(self.db, default_model_config_id)
            # api_key_config = api_keys[0] if api_keys else None
            api_key_config = await ModelApiKeyService.get_available_api_key_bridge_async(
                self.db,
                default_model_config_id,
                tenant_id=self.tenant_id,
            )

            if not api_key_config:
                logger.warning("Master Agent 没有可用的 API Key，降级为 smart 拼接（分块流式）")
                async for _evt in self._smart_merge_results_stream(results, strategy):
                    yield _evt
                return

            logger.info(
                "开始 Master Agent 流式整合",
                extra={
                    "agent_count": len(agent_responses),
                    "strategy": strategy
                }
            )

            # 创建 RedBearModelConfig（启用流式）
            # max_tokens 走 execution_config.merge_max_tokens（默认 8192）：
            # 写死 2000 会把长报告的整合结果截断，而整合输出正是用户看到的最终答案。
            _merge_params = self._resolve_merge_params()
            model_config = RedBearModelConfig.from_api_key(
                api_key_config,
                extra_params={
                    "temperature": _merge_params["temperature"],
                    "max_tokens": _merge_params["max_tokens"],
                    "streaming": True,
                },
            )

            # 创建 LLM 实例
            llm = RedBearLLM(model_config, type=ModelType.CHAT)

            logger.info("开始流式调用 Master Agent LLM")

            # 流式调用模型进行整合
            try:
                chunk_count = 0
                logger.debug(f"开始流式调用，provider={api_key_config.provider}")

                # 获取底层模型
                underlying_model = llm._model if hasattr(llm, '_model') else llm
                logger.debug(f"底层模型类型: {type(underlying_model).__name__}")

                # 使用底层模型的 astream 方法直接流式输出
                # 这样可以绕过可能的包装器累积问题
                async for chunk in underlying_model.astream(merge_prompt):
                    chunk_count += 1

                    # 提取内容
                    if hasattr(chunk, 'content'):
                        content = chunk.content
                    elif isinstance(chunk, str):
                        content = chunk
                    else:
                        content = str(chunk)

                    if content:
                        if chunk_count <= 5:
                            logger.debug(f"收到流式 chunk #{chunk_count}: {content[:30]}...")
                        yield self._format_sse_event("message", {"content": content})

                ModelApiKeyService.record_api_key_usage(self.db, api_key_config.id)

                logger.info(f"Master Agent 流式整合完成，共 {chunk_count} 个 chunks")

            except AttributeError as e:
                # 底层模型不支持 astream：退回一次性调用，但结果仍按块推送，
                # 避免主气泡"一坨突然出现"
                logger.warning(f"底层模型不支持流式，降级为分块输出: {str(e)}")
                response = await llm.ainvoke(merge_prompt)
                if hasattr(response, 'content'):
                    content = response.content
                else:
                    content = str(response)
                if not isinstance(content, str):
                    content = str(content)
                for chunk in self._chunk_text(content, self._SMART_MERGE_CHUNK_CHARS):
                    yield self._format_sse_event("message", {"content": chunk})
                    await asyncio.sleep(0)

        except Exception as e:
            logger.error(f"Master Agent 流式整合失败，降级为 smart 拼接: {str(e)}")
            # 降级到 smart 拼接，同样按块流式输出
            async for _evt in self._smart_merge_results_stream(results, strategy):
                yield _evt

    def _should_merge_results(
        self,
        results: List[Dict[str, Any]],
        strategy: str
    ) -> bool:
        """判断是否需要整合结果

        Args:
            results: Agent 执行结果
            strategy: 协作策略

        Returns:
            True 如果需要整合，False 如果不需要
        """
        if not results or len(results) == 1:
            # 没有结果或只有一个结果，不需要整合
            return False

        if strategy == "decomposition":
            # 问题拆分：每个子问题独立，用户已经看到所有答案
            # 通常不需要整合（除非配置要求）
            return self.config.execution_config.get("force_merge_decomposition", True)

        if strategy == "hierarchical":
            # 层级协作：主 Agent 已经整合了，不需要再整合
            return False

        # sequential 和 parallel 模式：可能需要整合去重
        return True

    async def _parallel_stream_agents(
        self,
        agent_tasks: List[Tuple[str, str, Any, str, Dict[str, Any]]],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str]
    ) -> AsyncIterator[Tuple[str, str, str, str]]:
        """并行流式执行多个 Agent，实时返回结果

        Args:
            agent_tasks: [(agent_id, agent_name, agent_config, message, context), ...]
            conversation_id: 会话 ID
            user_id: 用户 ID

        Yields:
            (agent_id, agent_name, event_type, content) 元组
        """
        # 为每个 Agent 创建异步生成器
        async def stream_single_agent(agent_id, agent_name, agent_config, message, context):
            """单个 Agent 的流式执行包装器"""
            try:
                async for event in self._execute_sub_agent_stream(
                    agent_config,
                    message,
                    context,
                    conversation_id,
                    user_id
                ):
                    # 解析事件
                    if "data:" in event:
                        try:
                            import json
                            data_line = event.split("data: ", 1)[1].strip()
                            data = json.loads(data_line)

                            if "content" in data:
                                yield (agent_id, agent_name, "content", data["content"])
                            elif _sse_event_name(event) in _CLUSTER_OBSERVABILITY_EVENTS:
                                # 并行执行时事件交错，必须带上 agent 归属再交给上层分流
                                yield (
                                    agent_id,
                                    agent_name,
                                    "raw",
                                    self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_name)),
                                )
                        except:
                            pass

                # 发送完成信号
                yield (agent_id, agent_name, "done", "")

            except Exception as e:
                logger.error(f"Agent {agent_name} 流式执行失败: {str(e)}")
                yield (agent_id, agent_name, "error", str(e))

        # 创建所有 Agent 的流式任务
        streams = []
        for agent_id, agent_name, agent_config, message, context in agent_tasks:
            stream = stream_single_agent(agent_id, agent_name, agent_config, message, context)
            streams.append(stream)

        # 使用队列来合并多个异步流
        queue = asyncio.Queue()
        active_streams = len(streams)

        async def consume_stream(stream, stream_id):
            """消费单个流并放入队列"""
            nonlocal active_streams
            try:
                async for item in stream:
                    await queue.put(item)
            finally:
                active_streams -= 1
                if active_streams == 0:
                    await queue.put(None)  # 所有流都完成了

        # 启动所有流的消费任务
        tasks = [
            asyncio.create_task(consume_stream(stream, i))
            for i, stream in enumerate(streams)
        ]

        # 从队列中读取并 yield
        while True:
            item = await queue.get()
            if item is None:  # 所有流都完成
                break
            yield item

        # 等待所有任务完成
        await asyncio.gather(*tasks, return_exceptions=True)

    def _calculate_similarity(self, messages: List[str]) -> float:
        """计算消息相似度（简化版）

        Args:
            messages: 消息列表

        Returns:
            相似度 (0-1)
        """
        if len(messages) < 2:
            return 0.0

        # 简化版：比较长度和关键词
        # 实际应用中可以使用更复杂的算法（如编辑距离、余弦相似度等）

        # 计算平均长度
        avg_length = sum(len(m) for m in messages) / len(messages)

        # 如果长度差异很大，认为不相似
        length_variance = sum(abs(len(m) - avg_length) for m in messages) / len(messages)
        if length_variance > avg_length * 0.5:
            return 0.3

        # 提取关键词（简化：取前50个字符）
        keywords = [m[:50] for m in messages]

        # 计算重复度
        unique_keywords = len(set(keywords))
        total_keywords = len(keywords)

        similarity = 1.0 - (unique_keywords / total_keywords)

        return similarity
