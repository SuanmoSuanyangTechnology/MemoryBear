"""多 Agent 编排器 - Master Agent 作为决策中心"""
import uuid
import time
import json
import asyncio
import inspect
import logging
from typing import Dict, Any, List, Optional, AsyncIterator, Tuple, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import MultiAgentConfig, AgentConfig, ModelConfig
from app.models.multi_agent_model import AggregationStrategy, OrchestrationMode
from app.services.agent_registry import AgentRegistry
from app.services.master_agent_router import MasterAgentRouter
from app.services.conversation_state_manager import (
    ConversationStateManager,
    create_conversation_state_manager,
)
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.repositories.tool_repository import ToolRepository
from app.services.model_service import ModelApiKeyService, ModelConfigService
from app.services.multi_agent_variable_contract import (
    ClusterVariableBag,
    build_cluster_variable_bag,
    render_variables_in_text,
)

logger = get_business_logger()

if TYPE_CHECKING:
    from app.models.models_model import ModelApiKey

# ──────────────────────────────────────────────────────────────────
# S8：supervisor 监督循环（ReAct 化）—— SubAgentTool 桥接层
# ──────────────────────────────────────────────────────────────────
# 主管 = LangChainAgent（agent 应用同引擎），工具列表 = 已发布的 agent 应用
# （agents-as-tools）。每个子 Agent 包成一个 BaseTool：调工具 = dispatch，
# 不调工具 = final；终止 = 模型不再调用工具（引擎原生语义）。
# 子 Agent 的上下文只来自主管给的任务书 + 变量包 —— 不携带集群会话历史
# （run_stream(sub_agent=True) skip_save 不写 messages，_ensure_conversation
# 只验证不新建，天然无串扰面）。

# 工具名 sanitize：LangChain 工具名不允许中文/空格（^[a-zA-Z0-9_-]+$），
# 中文名/空格映射为下划线，原名进 description 供主管选择。


def _sanitize_tool_name(name: str, agent_id: str) -> str:
    """子 Agent 工具名合法化（中文/空格→下划线，空名回退 agent_id）。"""
    cleaned = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "_-")) else "_"
        for ch in (name or "")
    ).strip("_")
    if not cleaned:
        cleaned = (agent_id or "agent").replace("-", "_")
    return f"delegate_{cleaned}"


# SubAgentConfig.role 同时承载两种语义：路由保留值（primary/secondary）和
# 前端「描述」输入框写入的自由文本，取简介时必须排除前者。
_RESERVED_SUB_AGENT_ROLES = frozenset({"primary", "secondary"})


def _sub_agent_brief(agent_data: Dict[str, Any]) -> str:
    """生成子 Agent 简介（供主管名册与工具描述使用）。

    来源优先级：
    1. 用户在集群里填的「描述」（存于 info["role"]，排除 primary/secondary 保留值）
       + 「能力」（info["capabilities"]）；
    2. info["description"]（兼容旧数据）；
    3. 子应用自身的描述（config.description）。
    都为空返回空串，由调用方决定兜底文案。
    """
    info = (agent_data or {}).get("info") or {}

    role = str(info.get("role") or "").strip()
    if role.lower() in _RESERVED_SUB_AGENT_ROLES:
        role = ""
    role = role.rstrip(" 。.;；")

    raw_caps = info.get("capabilities") or []
    if isinstance(raw_caps, str):
        raw_caps = [raw_caps]
    caps = [str(c).strip() for c in raw_caps if str(c).strip()]

    parts: List[str] = []
    if role:
        parts.append(role)
    if caps:
        parts.append("擅长：" + "、".join(caps))
    if parts:
        return "；".join(parts)

    legacy = str(info.get("description") or "").strip()
    if legacy:
        return legacy.rstrip(" 。.;；")

    app_desc = str(getattr((agent_data or {}).get("config"), "description", None) or "").strip()
    return app_desc.rstrip(" 。.;；")


class SubAgentTool:
    """子 Agent 工具的工厂/执行体（S8 桥接层核心）。

    用类方法而非闭包的原因：执行体需要访问 orchestrator 实例（账本/执行树/
    会话剥离），且要向主管循环回推可观测事件（agent_dispatch/agent_complete/
    agent_log）。事件经 event_sink 异步队列回推，主管循环侧统一格式化为 SSE。

    注意：LangChainAgent 在 __init__ 里会对每个工具做 `_wrap_tools_with_call_limit`
    包装（语言级护栏，第 N+1 次调用被拒并提示模型给最终答案）——工厂返回的必须是
    可被该包装识别的标准 BaseTool。
    """

    def __init__(
        self,
        orchestrator: "MultiAgentOrchestrator",
        agent_id: str,
        agent_name: str,
        description: str,
    ):
        self._orchestrator = orchestrator
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.description = description or agent_name
        # event_sink 由主管循环注入（asyncio.Queue，装 SSE 字符串）；
        # 工具执行期间产出的可观测事件都进这个队列，主管循环侧直接下发。
        self.event_sink: Optional[asyncio.Queue] = None
        # 工具展示名（LangChain 合法字符集）
        self.tool_name = _sanitize_tool_name(agent_name, agent_id)
        # 本轮被主管调用的次数（不是引擎计数器的副本 —— 引擎那份会在工具被拒后
        # 仍继续累加，这里只统计"真的跑了子 Agent"的次数，用于 loop_stop_reason
        # 判定：0 次=主管自答，达到上限=护栏触发）。
        self.calls = 0

    async def _emit_sse(self, event: str) -> None:
        """把子执行的可观测 SSE 事件送回主管循环（无 sink 时静默丢弃，不阻断执行）。"""
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink.put_nowait(event)
        except Exception:
            pass

    async def _execute(self, task: str) -> str:
        """工具执行体：跑子 Agent（非流式结果 + 流式可观测事件回推）。

        返回给主管 LLM 的字符串 = 子 Agent 的最终正文（全量，不做截断/转述——
        LangChain benchmark 结论：翻译层失真是 supervisor 主要错误源）。
        事件面：agent_dispatch（开跑）→ agent_log（轨迹快照，若干次）→
        agent_complete（收尾，带耗时/token/产出）。三类事件均带 agent 归属字段，
        前端 clusterStream.ts 按既有键名消费，零改动。
        """
        orch = self._orchestrator
        agent_data = orch.sub_agents.get(self.agent_id) if orch else None
        if not agent_data:
            return f"子 Agent 不可用: {self.agent_name}（{self.agent_id}）"

        # max_iterations = 主管循环轮次上限（一轮 = 主管一次决策里发出的那批子 Agent 调用，
        # 同一轮并发派 N 个只算 1 轮）。达到上限后新一轮的调用不再执行，把限制写进
        # 工具返回值，让主管基于已有结果收尾——与引擎 _wrap_tools_with_call_limit 同一机制，
        # 避免撞 LangGraph recursion_limit 后被引擎吞成一句通用道歉。
        #
        # 轮次判定：下一轮的 LLM 决策必须等上一批工具全部返回，所以"调用开始时没有
        # 在途的子 Agent 调用"即新一轮的第一个调用；同批后续调用开始时前者仍在途。
        # 检查与自增之间没有 await，同批并发调用不会互相干扰。
        round_limit = orch._resolve_loop_max_iterations()
        inflight = int(getattr(orch, "_loop_inflight", 0) or 0)
        rounds = int(getattr(orch, "_loop_rounds", 0) or 0)
        if inflight == 0:
            if round_limit is not None and rounds >= round_limit:
                orch._loop_iteration_limit_hit = True
                logger.warning(
                    "S10 supervisor_loop：主管循环轮次达到 max_iterations，拒绝继续分派",
                    extra={"agent": self.agent_name, "limit": round_limit},
                )
                return (
                    f"[轮次限制] 主管循环已达 {round_limit} 轮上限，"
                    f"不再执行「{self.agent_name}」。请勿再调用任何子 Agent，"
                    f"直接基于已有结果给出最终答案。"
                )
            orch._loop_rounds = rounds + 1
        orch._loop_inflight = inflight + 1
        try:
            return await self._run_dispatch(orch, agent_data, task)
        finally:
            orch._loop_inflight = max(0, int(getattr(orch, "_loop_inflight", 1) or 1) - 1)

    async def _run_dispatch(self, orch, agent_data, task: str) -> str:
        """执行一次子 Agent 分派（轮次闸门之后的原执行体）。"""
        self.calls += 1
        loop_started = time.time()

        # 子 Agent 上下文 = 主管任务书 + 变量包（拍板：不携带集群会话历史）。
        # 变量包在入口已做过校验/默认值兜底，这里原样下发：子 Agent 层再按自己的
        # 定义补默认值（prepare_variables_for_cluster），不再重复裁决必填。
        _bag = getattr(orch, "_cluster_variables", None)
        context = dict(_bag.values) if _bag is not None else {}
        sub_text = ""
        sub_error: Optional[str] = None

        # 走流式执行体（保 S2 执行树 / S3 会话剥离 / S4 账本口径），
        # 累积正文作为返回值；可观测事件直接转发 run_stream 原生 SSE
        #（agent_dispatch/agent_complete 自带 execution_id——比手工重建的
        # 裸事件绑定更准，且 draft_run 侧的收尾/轨迹字段齐全）。
        # start/end 属于子运行生命周期（_SUB_RUN_LIFECYCLE_EVENTS），过滤不透传。
        #
        # 隔离 LangChain 运行上下文（关键）：本执行体作为工具跑在主管引擎
        # astream_events 的回调上下文里，子 Agent 的 LangChainAgent 若经
        # var_child_runnable_config 继承父级 callbacks，其 on_chat_model_stream
        # 会被主管的 astream_events 当成主管正文 yield —— loop 层随之转成
        # event: message 发给前端，主气泡混入子 Agent 原始输出，full_content
        # 落库正文同样被污染。与 workflow/base.py、content_search.py 的隔离
        # 手法一致：子运行挂空 callbacks/tags/metadata，事件只进自己的流。
        from langchain_core.runnables.config import (
            RunnableConfig,
            var_child_runnable_config,
        )

        _config_token = var_child_runnable_config.set(
            RunnableConfig(callbacks=[], tags=[], metadata={})
        )
        try:
            async for event in orch._execute_sub_agent_stream(
                agent_data["config"],
                task,
                context,
                orch.current_conversation_id,
                getattr(orch, "_loop_user_id", None),
                getattr(orch, "_loop_web_search", False),
                getattr(orch, "_loop_memory", True),
                getattr(orch, "_loop_storage_type", ""),
                getattr(orch, "_loop_user_rag_memory_id", ""),
            ):
                name = _sse_event_name(event)
                if name in ("message", "sub_agent_message"):
                    try:
                        data = json.loads(event.split("data: ", 1)[1].strip())
                        sub_text += data.get("content") or ""
                    except Exception:
                        pass
                elif name in _SUB_RUN_LIFECYCLE_EVENTS or name == "sub_usage":
                    continue
                elif name in _CLUSTER_OBSERVABILITY_EVENTS:
                    # 剥离子会话 ID（S3），再补 Agent 归属（与三段式 `_execute_supervisor_stream`
                    # 的注入同款）：agent_dispatch/agent_complete 原生自带
                    # execution_id/agent_id/agent_name（setdefault 不覆盖自带值），
                    # 但 agent_log/agent_log_final 的 data 只有 {type, data}——缺归属时
                    # 前端 findAgentBlockIndex 匹配不到 dispatch 建立的区块，轨迹会落到
                    # 游离的 unknown 块，子 Agent 区块的 agent_log 恒为空 → Runtime 的
                    # 轮次详情入口不出现，工具调用流程无从展示。
                    await self._emit_sse(orch._inject_agent_meta(
                        orch._strip_conversation_id(event),
                        orch._sub_event_meta(self.agent_id, self.agent_name),
                    ))
        except Exception as e:
            sub_error = str(e)
            logger.error(
                "S8 loop：子 Agent 执行失败",
                extra={"agent_id": self.agent_id, "error": sub_error},
                exc_info=True,
            )
        finally:
            var_child_runnable_config.reset(_config_token)

        # 兜底收尾：run_stream 异常中断时 agent_complete 可能没发出去，
        # 前端区块会停在 running —— 手工补一条裸收尾事件。
        if sub_error:
            await self._emit_sse(orch._format_sse_event("agent_complete", {
                "agent_id": self.agent_id,
                "agent_name": self.agent_name,
                "parent_execution_id": str(orch.current_execution_id) if orch.current_execution_id else None,
                "orchestration_mode": orch._normalized_mode,
                "status": "failed",
                "error": sub_error[:500],
                "elapsed_time": round(time.time() - loop_started, 2),
            }))

        if sub_error:
            return f"子 Agent {self.agent_name} 执行失败: {sub_error[:500]}"
        return sub_text or f"（子 Agent {self.agent_name} 未返回内容）"

    def build(self) -> "BaseTool":  # noqa: F821 - 延迟类型引用
        """构造 LangChain BaseTool（异步执行体 + 中文描述）。

        sink 挂接方式：主管循环经 orchestrator._loop_tool_instances 实例列表
        直达 SubAgentTool（_build_supervisor_agent 填充），不从 LangChain 工具
        对象反查——引擎 _wrap_tools_with_call_limit 会就地包装 _run/_arun，
        从工具对象反查闭包不可靠。
        """
        from langchain_core.tools import tool as lc_tool
        from pydantic import BaseModel, Field

        instance = self

        class _TaskInput(BaseModel):
            task: str = Field(
                default="",
                description="派给该子 Agent 执行的具体任务/问题（一句话说清目标与范围，子 Agent 只看到这段任务书）",
            )

        tool_name = self.tool_name

        @lc_tool(tool_name, args_schema=_TaskInput)
        async def delegate_sub_agent(task: str = "") -> str:
            """把子任务派给该子 Agent 执行，返回它的完整输出。"""
            return await instance._execute(task or "")

        # 中文名/角色描述写在 description 里（工具名受 ^[a-zA-Z0-9_-]+$ 限制，
        # 主管只能靠 description 认人）。docstring 兜底 + 显式赋值双保险：
        # 某些 langchain 版本在 description 为空时会拒绝建工具。
        delegate_sub_agent.description = (
            f"子 Agent「{self.agent_name}」。{self.description}。"
            f"调用它执行你无法直接完成的子任务，返回该 Agent 的完整输出。"
        )
        return delegate_sub_agent


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


def _trace_total_tokens(agent_log_item: Any) -> int:
    """从 LangChainAgent 的 agent_log 事件里取"逐轮累加"的 token 总数。

    引擎 `chat_stream` 最终 yield 的 int 只统计**最后一次** LLM 调用
    （`AgentTraceRecorder.finalize` 会用末次值覆盖 meta.total_tokens），主管多轮
    循环时直接取末值会漏账。iterations[].llm.tokens 是 `finish_llm` 逐轮累加进去的
    （langchain_agent.py:149），用它对齐 S4 账本。取不到返回 0。
    """
    if not isinstance(agent_log_item, dict):
        return 0
    data = agent_log_item.get("data")
    if not isinstance(data, dict):
        return 0
    iterations = data.get("iterations")
    if not isinstance(iterations, list):
        return 0
    total = 0
    for iteration in iterations:
        if not isinstance(iteration, dict):
            continue
        llm = iteration.get("llm")
        if isinstance(llm, dict):
            try:
                total += int(llm.get("tokens") or 0)
            except (TypeError, ValueError):
                pass
    return total


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
        # execution_config 快照：流式执行中途 ORM 会话可能 commit 使 config 属性过期，
        # 之后再读 self.config.execution_config 会触发同步懒加载并抛 MissingGreenlet
        # （异步会话下不允许）。构造时属性仍是已加载状态，这里拷一份纯 dict 供运行期读取。
        _exec_cfg = getattr(config, "execution_config", None)
        self._execution_config: Dict[str, Any] = dict(_exec_cfg) if isinstance(_exec_cfg, dict) else {}
        # 同理快照运行期会再读的标量/JSON 字段：流式收尾阶段（cluster_memory_enabled、
        # 协作模式激活记录等）config 已过期，直接读 ORM 属性会同样抛 MissingGreenlet。
        _sup_cfg = getattr(config, "supervisor_config", None)
        self._supervisor_config_snapshot: Dict[str, Any] = _sup_cfg if isinstance(_sup_cfg, dict) else {}
        self._app_id = getattr(config, "app_id", None)
        self._master_agent_id = getattr(config, "master_agent_id", None)
        self._master_agent_name = getattr(config, "master_agent_name", None)
        self.tenant_id = None
        self.sub_agents = {}
        # create() 解析后的子 Agent 条目（agent_id 已换成有效 release ID）；
        # 未经 create() 构造时为 None，下游回退用原始配置。
        self._effective_sub_agent_entries: Optional[List[Dict[str, Any]]] = None
        # P0-1：路由状态后端。这里只放内存兜底，真正的 Redis 后端在 create() 里
        # 等 tenant_id 解析完成后注入（key 前缀需要租户隔离）。
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
        # S3：本轮整合实际执行的模式（master/smart/skip），execute_stream 结束时进
        # end 事件（merge_mode_actual 字段）。None=模式未走到整合阶段（协作模式等）。
        self._merge_mode_actual: Optional[str] = None
        # S4：per-turn token 账本（UsageLedger v1）。
        # 三层：routing（Master 路由决策）/ sub（子 Agent 执行）/ merge（结果整合）。
        # 计入点集中在一处包装层（_execute_sub_agent_stream/_execute_sub_agent），
        # 各调用分支无需各自解析 sub_usage 字符串 —— 外层入口与前端一律从
        # end 事件的 usage 字段读总数，禁止再拼 SSE 字符串统计。
        # prompt/completion 目前各发射器只有 total，保持 0。
        self._turn_routing_tokens: int = 0
        self._turn_sub_tokens: int = 0
        self._turn_merge_tokens: int = 0
        # S5：本轮生效的集群变量包（入口校验/渲染的产物），两种模式共用：
        # supervisor 进 task_analysis["initial_context"]，collaboration 进 handoffs
        # 节点的 system_prompt / 消息渲染。观测（缺失必填、未定义变量）也挂在它身上。
        self._cluster_variables: Optional[ClusterVariableBag] = None
        # S8/S9：主管监督循环（orchestration_mode=supervisor_loop）的运行态。
        # _loop_tool_instances：本轮 SubAgentTool 执行体列表（工具事件 sink 挂接 +
        # 调用次数统计）；_loop_stop_reason：本轮终止原因（自答 / 派发后收尾 /
        # 护栏触发），进 end 事件供护栏可观测。
        # S9：删除 _supervisor_loop_fallback —— 循环不再回退三段式，失败直接抛错。
        self._loop_tool_instances: list = []
        self._loop_stop_reason: Optional[str] = None
        # max_iterations = 主管循环轮次上限。_loop_rounds：本轮已开始的主管轮次；
        # _loop_inflight：当前在途的子 Agent 调用数（为 0 时下一次调用即新一轮）；
        # _loop_iteration_limit_hit：轮次闸门拒绝过调用（SubAgentTool 写入），
        # 用于把 loop_stop_reason 准确记成 max_iterations（软限制不会抛 GraphRecursionError）
        self._loop_rounds: int = 0
        self._loop_inflight: int = 0
        self._loop_iteration_limit_hit: bool = False
        # 对话能力（features）：对齐 Agent 应用。存量集群无 features → 空 dict = 全部关闭，行为不变。
        from app.services.cluster_chat_features import get_features as _get_cluster_features
        self._features: Dict[str, Any] = _get_cluster_features(self._supervisor_config_snapshot)
        # 主管联网 = features.web_search.enabled 且请求 web_search（子 Agent 联网仍走 _loop_web_search）
        self._loop_supervisor_web_search: bool = False
        # 深度思考 = model_parameters.deep_thinking 且请求 thinking
        self._loop_deep_thinking: bool = False
        # 新会话开场白（入口解析后传入，拼到主管历史末尾；None = 不注入）
        self._loop_opening_statement: Optional[str] = None
        # 执行模式：集群主管目前只有 in_process 实现，sandbox 请求会回退并在 end 事件显式标注
        self._execution_mode_requested: Optional[str] = None
        self._execution_mode_actual: str = "in_process"
        # F5-F8 本轮运行态（建议问题/引用/TTS/情绪）；每轮在 _prepare_turn_chat_features 重置
        self._loop_citations: list = []
        self._turn_extras: Dict[str, Any] = {}
        self._turn_emotion_detection: Any = None
        self._turn_user_message_id: Optional[uuid.UUID] = None
        # F10 context_engine：仅 features.context_engine.enabled 且插件可用时才会被填充
        self._turn_ctx_history: Optional[List[Dict[str, Any]]] = None
        self._turn_used_context_engine: bool = False
        self._turn_ctx_provider: Optional[str] = None
        self._turn_ctx_model_config_id: Any = None

    def _prepare_turn_chat_features(
        self,
        web_search: bool,
        thinking: bool,
        opening_statement: Optional[str],
        execution_mode: Optional[str],
        user_message_id: Optional[uuid.UUID] = None,
    ) -> None:
        """每轮开始时解析"对话能力"运行态（对齐 Agent 应用）。

        向后兼容：存量集群无 features、请求不带 thinking/opening/execution_mode 时，
        结果全部为关闭/None，主管行为与改动前完全一致。
        """
        from app.services.cluster_chat_features import (
            effective_execution_mode,
            is_deep_thinking_on,
            is_supervisor_web_search_on,
        )

        self._loop_supervisor_web_search = is_supervisor_web_search_on(self._features, web_search)
        self._loop_deep_thinking = is_deep_thinking_on(self.model_parameters, thinking)
        self._loop_opening_statement = opening_statement or None
        self._execution_mode_requested = execution_mode
        actual, fell_back = effective_execution_mode(execution_mode)
        self._execution_mode_actual = actual
        if fell_back:
            logger.warning("集群主管暂不支持 sandbox 执行模式，已回退 in_process")
        # F5-F8：每轮重置，避免多轮复用同一 orchestrator 时串轮
        self._loop_citations = []
        self._turn_extras = {}
        self._turn_emotion_detection = None
        self._turn_user_message_id = user_message_id
        # F10：每轮重置 context_engine 运行态
        self._turn_ctx_history = None
        self._turn_used_context_engine = False
        self._turn_ctx_provider = None
        self._turn_ctx_model_config_id = None

    def context_engine_after_turn_args(self, conversation_id: Any) -> Optional[Dict[str, Any]]:
        """本轮若走了 context_engine，返回 BatchPersistQueue "after_turn" 任务参数；否则 None（老集群恒为 None）。"""
        if not self._turn_used_context_engine or not conversation_id:
            return None
        return {
            "conversation_id": str(conversation_id),
            "features_config": self._features,
            "api_key_provider": self._turn_ctx_provider,
            "model_config_id": str(self._turn_ctx_model_config_id) if self._turn_ctx_model_config_id else None,
            "scope_key": "cluster",
        }

    async def _prepare_context_engine(
        self,
        conversation_id: Optional[uuid.UUID],
        system_prompt: str,
        api_key_config: Any,
    ) -> str:
        """features.context_engine 开启时，用上下文引擎生成主管 system_prompt（含摘要）与历史。

        未开启 / 插件缺失 / 任何异常 → 原样返回 system_prompt，history 留空，
        调用方继续走 _load_cluster_history（与改动前一致）。
        """
        ctx_cfg = (self._features or {}).get("context_engine")
        if not (isinstance(ctx_cfg, dict) and ctx_cfg.get("enabled")) or not conversation_id or self.db is None:
            return system_prompt
        try:
            from app.core.config import settings
            from app.services.context_engine_manager import ContextEngineManager

            provider = getattr(api_key_config, "provider", None)
            model_config_id = getattr(api_key_config, "model_config_id", None) or self.default_model_config_id
            prepared = await ContextEngineManager(self.db).prepare_app_agent_input(
                features=self._features,
                conversation_id=conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id)),
                system_prompt=system_prompt,
                current_input=getattr(self, "_loop_entry_message", "") or "",
                current_provider=provider,
                legacy_max_history=settings.AGENT_MAX_HISTORY,
                scope_key="cluster",
                model_config_id=model_config_id,
            )
            if not prepared:
                return system_prompt
            new_prompt, history = prepared
            # 与 _load_cluster_history 同口径：主管链路只吃纯文本轮
            self._turn_ctx_history = [
                {"role": h["role"], "content": h["content"]}
                for h in (history or [])
                if isinstance(h.get("content"), str)
            ]
            self._turn_used_context_engine = True
            self._turn_ctx_provider = provider
            self._turn_ctx_model_config_id = model_config_id
            return new_prompt or system_prompt
        except Exception as e:  # noqa: BLE001 - 上下文引擎是增量能力，降级回旧历史
            logger.warning(f"集群 context_engine 准备失败（已降级为旧历史）: {e}")
            self._turn_ctx_history = None
            self._turn_used_context_engine = False
            return system_prompt

    async def _resolve_loop_history(self, conversation_id: Optional[uuid.UUID]) -> List[Dict[str, Any]]:
        """主管历史：context_engine 本轮生效则用其结果，否则沿用集群 20 轮历史；再拼开场白。"""
        if self._turn_used_context_engine and self._turn_ctx_history is not None:
            return self._with_opening(list(self._turn_ctx_history))
        return self._with_opening(await self._load_cluster_history(conversation_id))

    def _turn_end_extras(self) -> Dict[str, Any]:
        """end 事件的对话能力附加字段；无任何新能力生效时为空 dict（旧前端/旧契约不变）。"""
        extras: Dict[str, Any] = {}
        if self._execution_mode_requested:
            extras["execution_mode_actual"] = self._execution_mode_actual
        # suggested_questions / citations / audio_url / audio_status：仅对应 features 开启时才有键
        extras.update(self._turn_extras or {})
        return extras

    def _supervisor_api_key_dict(self, api_key_config: Any) -> Dict[str, Any]:
        """主管 ModelApiKey 运行时壳 → dict（建议问题 / TTS 复用 Agent 应用的同形状构造）。"""

        def _strs(items: Any) -> List[str]:
            return [str(i) for i in (items or [])]

        return {
            "model_name": getattr(api_key_config, "model_name", None),
            "api_key": getattr(api_key_config, "api_key", None),
            "provider": getattr(api_key_config, "provider", None) or "openai",
            "api_base": getattr(api_key_config, "api_base", None),
            "input_modalities": _strs(getattr(api_key_config, "input_modalities", None)),
            "output_modalities": _strs(getattr(api_key_config, "output_modalities", None)),
            "features": _strs(getattr(api_key_config, "features", None)),
            "tenant_id": getattr(api_key_config, "tenant_id", None),
            "model_config_id": getattr(api_key_config, "model_config_id", None),
            "channel_id": getattr(api_key_config, "channel_id", None),
        }

    def _start_turn_emotion_detection(self, message: str) -> None:
        """features.emotion_reply 开启才起后台情绪识别任务；关闭返回 None，零开销。"""
        try:
            from app.core.memory.emotion.emotion_resolver import start_detection

            self._turn_emotion_detection = start_detection(self._features, message)
        except Exception as e:  # noqa: BLE001 - 情绪感知是增量能力，失败不阻断对话
            logger.warning(f"集群情绪识别启动失败（已跳过）: {e}")
            self._turn_emotion_detection = None

    async def _setup_turn_tts(self, api_key_dict: Dict[str, Any]):
        """features.text_to_speech 开启才建流式 TTS；返回 (text_queue, audio_url, tts_task)。"""
        tts_cfg = (self._features or {}).get("text_to_speech")
        if not (isinstance(tts_cfg, dict) and tts_cfg.get("enabled")):
            return None, None, None
        try:
            from app.services.draft_run_service import AgentRunService

            text_queue: asyncio.Queue = asyncio.Queue()
            audio_url, tts_task = await AgentRunService(self.db)._generate_tts_streaming(
                self._features,
                api_key_dict,
                text_queue=text_queue,
                tenant_id=self.tenant_id,
                workspace_id=await self._cluster_workspace_id(),
            )
            if tts_task is None:
                return None, None, None
            return text_queue, audio_url, tts_task
        except Exception as e:  # noqa: BLE001
            logger.warning(f"集群 TTS 初始化失败（已跳过）: {e}")
            return None, None, None

    async def _finalize_turn_extras(
        self,
        final_content: str,
        api_key_dict: Dict[str, Any],
        audio_url: Optional[str] = None,
        tts_task: Optional["asyncio.Task"] = None,
        non_stream_tts: bool = False,
    ) -> None:
        """主管正文结束后产出 end 附加字段：建议问题 / 引用 / 语音。

        各项仅在对应 features 开启时才写入 self._turn_extras（缺省关闭 = 无任何新键，契约不变）。
        任何一项失败只记 warning，不影响正文与 end 事件。
        """
        features = self._features or {}
        extras: Dict[str, Any] = {}
        try:
            from app.services.draft_run_service import AgentRunService

            agent_service = AgentRunService(self.db)

            sq_cfg = features.get("suggested_questions_after_answer")
            if isinstance(sq_cfg, dict) and sq_cfg.get("enabled"):
                extras["suggested_questions"] = await agent_service._generate_suggested_questions(
                    features, final_content, api_key_dict, {}
                )

            cit_cfg = features.get("citation")
            if isinstance(cit_cfg, dict) and cit_cfg.get("enabled"):
                # 仅主管自己知识库的引用；子 Agent 内部检索不聚合
                extras["citations"] = agent_service._filter_citations(
                    features, list(self._loop_citations or [])
                )

            tts_cfg = features.get("text_to_speech")
            if non_stream_tts and isinstance(tts_cfg, dict) and tts_cfg.get("enabled"):
                audio_url = await agent_service._generate_tts(
                    features,
                    final_content,
                    api_key_dict,
                    tenant_id=self.tenant_id,
                    workspace_id=await self._cluster_workspace_id(),
                )
                tts_task = None
            if audio_url:
                status = "pending"
                if tts_task is not None and tts_task.done():
                    try:
                        tts_task.result()
                        status = "completed"
                    except Exception:  # noqa: BLE001
                        status = "failed"
                extras["audio_url"] = audio_url
                extras["audio_status"] = status
        except Exception as e:  # noqa: BLE001
            logger.warning(f"集群对话附加能力（建议问题/引用/TTS）生成失败（已跳过）: {e}")
        self._turn_extras = extras

    def _with_opening(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """新会话开场白拼到历史末尾（与 Agent 应用一致）；无开场白时原样返回。"""
        if not self._loop_opening_statement:
            return history
        return list(history or []) + [{"role": "assistant", "content": self._loop_opening_statement}]

    async def _prepare_loop_files(
        self,
        files: Optional[List[Any]],
        api_key_config: Any,
        message: str,
        supervisor: Any,
    ) -> tuple:
        """主管本轮附件处理（对齐 Agent 应用：校验 → MultimodalService → 图片清单）。

        文件只给主管（子 Agent 不接收）。向后兼容：无 files 直接返回 (message, None)，
        不触发任何新逻辑；有 files 但集群未开启 features.file_upload 则明确报错，
        与 Agent 应用"该应用未开启文件上传功能"同口径。

        Returns:
            (llm_message, processed_files)：llm_message 为追加了图片清单的用户消息；
            processed_files 为 LLM 可用的多模态内容（供 chat/chat_stream 的 files 参数）。
        """
        # 本轮落库用（入口层读取 history_files / provider）
        self._turn_processed_files = None
        self._turn_files_provider = None
        if not files:
            return message, None

        fu = self._features.get("file_upload") if isinstance(self._features, dict) else None
        if not (isinstance(fu, dict) and fu.get("enabled")):
            raise BusinessException("该集群未开启文件上传功能", BizCode.BAD_REQUEST)

        from app.models import ModelType
        from app.schemas.model_schema import ModelInfo
        from app.services.draft_run_service import AgentRunService, build_uploaded_images_manifest
        from app.services.multimodal_service import MultimodalService

        AgentRunService._validate_file_upload(self._features, files)

        provider = getattr(api_key_config, "provider", None) or "openai"
        model_info = ModelInfo(
            model_name=api_key_config.model_name,
            provider=provider,
            api_key=api_key_config.api_key,
            api_base=api_key_config.api_base,
            input_modalities=[str(i) for i in (getattr(api_key_config, "input_modalities", None) or [])],
            output_modalities=[str(i) for i in (getattr(api_key_config, "output_modalities", None) or [])],
            features=[str(i) for i in (getattr(api_key_config, "features", None) or [])],
            model_type=ModelType.LLM,
            tenant_id=getattr(api_key_config, "tenant_id", None),
            model_config_id=getattr(api_key_config, "model_config_id", None),
            channel_id=getattr(api_key_config, "channel_id", None),
            failover_plan=getattr(api_key_config, "failover_plan", None),
        )
        multimodal_service = MultimodalService(self.db, model_info)
        processed_files = await multimodal_service.process_files(
            files,
            document_image_recognition=bool(fu.get("document_image_recognition", False)),
            workspace_id=await self._cluster_workspace_id(),
            file_upload_config=fu,
        )
        logger.info(f"集群主管处理了 {len(processed_files)} 个文件")

        # 本轮图片白名单回注给主管的知识库工具（以图搜图）
        for tool in getattr(supervisor, "tools", None) or []:
            set_uploaded_files = getattr(tool, "set_uploaded_files", None)
            if callable(set_uploaded_files):
                set_uploaded_files(files)

        # 用户消息里列出本轮图片及编号，供模型按需显式触发图片检索（不污染入库原文）
        llm_message = message
        image_manifest, _ = build_uploaded_images_manifest(files)
        if image_manifest:
            llm_message = f"{message}\n\n{image_manifest}"

        self._turn_processed_files = processed_files
        self._turn_files_provider = provider
        return llm_message, processed_files

    def _build_cluster_variables(
        self,
        variables: Optional[Dict[str, Any]],
    ) -> ClusterVariableBag:
        """入口变量契约：定义收集 → 必填校验 → 默认值兜底 → 可观测留痕。

        每轮都重算（同一 orchestrator 实例会跑多轮，且子 Agent 配置可能已更新）。
        """
        bag = build_cluster_variable_bag(self.config, self.sub_agents, variables)
        self._cluster_variables = bag
        return bag

    def _render_entry_message(self, message: str, values: Optional[Dict[str, Any]]) -> str:
        """入口消息渲染：只替换变量包里确实存在的 {{name}}。

        渲染后的文本才是下发给子 Agent / handoffs 的内容 —— 此前用户消息里的
        {{变量}} 从未被渲染，配了变量也不生效。用户自己输入的花括号
        不在变量包里，会原样保留。
        """
        return render_variables_in_text(message or "", values or {})

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
        if self.db is None or not self._app_id or not conversation_id:
            return None
        try:
            from app.services.draft_run_service import AgentRunService

            conv_uuid = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
            svc = AgentRunService(self.db)
            self.current_execution_id = await svc._create_agent_execution_async(
                app_id=self._app_id,
                conversation_id=conv_uuid,
                # 主 Agent 拿不到 agent_configs.id：config.master_agent_id 本身就是 release id
                agent_config_id=None,
                started_at=utcnow_naive(),
                model_name=(self.master_model_config.name if self.master_model_config else "") or "",
                provider=None,
                agent_role="master",
                parent_execution_id=None,
                orchestration_mode=self._normalized_mode,
                release_id=self._master_agent_id,
                agent_name=self._master_agent_name or "主编排",
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
        """收尾主 execution 记录；失败同样只记 warning。

        S5：把本轮变量契约的观测结果（定义来源、缺失必填、默认值兜底、未定义变量）
        一并写进 meta_data —— 集群场景里"配了变量没生效"的归因此前完全没有落地点。
        """
        execution_id = self.current_execution_id
        if execution_id is None or self.db is None:
            return
        try:
            from app.services.draft_run_service import AgentRunService

            svc = AgentRunService(self.db)
            meta_patch: Dict[str, Any] = {}
            if self._cluster_variables is not None:
                meta_patch["variables"] = self._cluster_variables.to_observation()
            if self._loop_stop_reason:
                meta_patch["loop_stop_reason"] = self._loop_stop_reason
            meta_patch = meta_patch or None
            await svc._update_agent_execution_completed_async(
                execution_id=execution_id,
                steps=[],
                status=status,
                elapsed_time=elapsed_time,
                token_usage=token_usage,
                error_message=error_message,
                meta_patch=meta_patch,
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
        if self.db is None or not self._app_id or not self.current_conversation_id:
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
                app_id=self._app_id,
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

    async def _open_and_close_collaboration_activation(
        self,
        agent_configs: Dict[str, Any],
        agent_key: Optional[str],
        display_name: Optional[str],
        task: str,
        status: str = "completed",
        elapsed_time: Optional[float] = None,
        token_usage: Optional[dict] = None,
        steps: Optional[list] = None,
    ) -> None:
        """协作模式（非流式）：一次性补记一条子 Agent 激活记录。

        非流式路径拿不到逐激活的实时事件（agent/handoff/end），运行结束后按
        handoff_history 逐条"先开后收"。与流式路径共用 _open/_close，
        保证两种模式的 agent_executions 树结构一致（master + N×sub）。
        观测失败同样只降级不阻断。
        """
        execution_id, _ = await self._open_collaboration_activation(
            agent_configs, agent_key, display_name, task
        )
        if execution_id is not None:
            await self._close_collaboration_activation(
                execution_id, status, elapsed_time,
                token_usage=token_usage, steps=steps,
            )

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

        # P0-1：租户确定后再建状态管理器 —— key 前缀 `conv_state:{tenant}:{conv}`
        # 需要 tenant_id；Redis 不可用时工厂内部降级为内存。
        orchestrator.state_manager = create_conversation_state_manager(
            tenant_id=str(orchestrator.tenant_id) if orchestrator.tenant_id else None
        )

        from app.services.multi_agent_release_resolver import aresolve_effective_release_id

        effective_entries: List[Dict[str, Any]] = []
        for sub_agent_info in config.sub_agents:
            # 版本策略：库里 agent_id 是子 Agent 的应用 ID（旧数据为 release ID，由解析层按值判别），
            # 这里按 release_policy 解析出有效 release：current=应用此刻的发布版本，pinned=release_id。
            # info 用拷贝，不改配置对象；有效 release ID 写回 info["agent_id"]，
            # 运行时内部（sub_agents 的键、路由、执行记录）统一以它为键。
            # 非 strict：旧形态 current 解析失败回退锚点版本，不让一次下线拖垮整个集群。
            if db is not None:
                agent_id = await aresolve_effective_release_id(db, sub_agent_info, strict=False)
            else:
                agent_id = uuid.UUID(str(sub_agent_info["agent_id"]))
            sub_agent_info = {**sub_agent_info, "agent_id": str(agent_id)}
            effective_entries.append(sub_agent_info)
            agent = await orchestrator._load_agent_async(agent_id)
            orchestrator.sub_agents[str(agent_id)] = {
                "config": agent,
                "info": sub_agent_info
            }
        # 下游（任务分析 / 协作 handoffs）按 release ID 对应 sub_agents，必须用解析后的条目
        orchestrator._effective_sub_agent_entries = effective_entries

        # S9：两个"主管类"模式都必须有主模型——supervisor 用于路由/整合，
        # supervisor_loop 用于驱动主管 ReAct 引擎（_resolve_supervisor_api_key）。
        if orchestrator._normalized_mode in (
            OrchestrationMode.SUPERVISOR,
            OrchestrationMode.SUPERVISOR_LOOP,
        ):
            if not orchestrator.default_model_config_id:
                raise BusinessException("Supervisor 模式需要配置默认模型", BizCode.AGENT_CONFIG_MISSING)

            orchestrator.master_model_config = await orchestrator._db_get(
                ModelConfig,
                orchestrator.default_model_config_id,
            )
            if not orchestrator.master_model_config:
                raise BusinessException("Master Agent 模型配置不存在", BizCode.AGENT_CONFIG_MISSING)

        # MasterAgentRouter 只服务三段式：supervisor_loop 的分派全权归主管 LLM，
        # 不烧路由调用（S9：循环从 supervisor 内部分支上提为独立模式后更清晰）。
        if orchestrator._normalized_mode == OrchestrationMode.SUPERVISOR:
            orchestrator.router = MasterAgentRouter(
                db=db,
                master_model_config=orchestrator.master_model_config,
                model_parameters=orchestrator.model_parameters,
                sub_agents=orchestrator.sub_agents,
                state_manager=orchestrator.state_manager,
                tenant_id=orchestrator.tenant_id,
                # 关键词规则快路径已删除，不再传 enable_rule_fast_path
                #（MasterAgentRouter 已移除该参数）
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
            标准化后的模式：collaboration / supervisor / supervisor_loop
        """
        # S9：supervisor_loop 必须先判——它是 supervisor 的前缀超串，
        # 顺序颠倒会被 supervisor 吞掉（回归测试锁定此不变量）。
        if mode in [OrchestrationMode.SUPERVISOR_LOOP, "supervisor_loop"]:
            return OrchestrationMode.SUPERVISOR_LOOP
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
        message_id: Optional[uuid.UUID] = None,
        thinking: bool = False,
        opening_statement: Optional[str] = None,
        execution_mode: Optional[str] = None,
        files: Optional[List[Any]] = None,
        user_message_id: Optional[uuid.UUID] = None,
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

        # S3：每轮开始重置整合模式观测值（同一 orchestrator 实例可能跑多轮）
        self._merge_mode_actual = None
        # S4：每轮重置 token 账本（多轮复用同一 orchestrator 实例时口径不串轮）
        self._turn_routing_tokens = 0
        self._turn_sub_tokens = 0
        self._turn_merge_tokens = 0
        # S8/S9：每轮重置主管循环运行态（护栏判定不跨轮）
        self._loop_stop_reason = None
        # 对话能力运行态（联网/深度思考/开场白/执行模式）；全部缺省 = 旧行为
        self._prepare_turn_chat_features(
            web_search, thinking, opening_statement, execution_mode, user_message_id
        )

        # S5：入口变量契约 —— 一次校验 + 默认值兜底 + 渲染本轮消息。
        # 两模式共用同一份变量包，用户"配了变量就该生效"的感知因此与单 Agent 应用一致。
        cluster_variables = self._build_cluster_variables(variables)
        rendered_message = self._render_entry_message(message, cluster_variables.values)

        logger.info(
            "开始执行多 Agent 任务（流式）",
            extra={
                "mode": self._normalized_mode,
                "message_length": len(message),
                "variables": cluster_variables.to_observation(),
            }
        )

        # 主执行记录：子 Agent 记录需要它作为 parent，日志详情也需要它来挂载节点
        await self._ensure_master_execution(conversation_id, message_id)

        try:
            # 发送开始事件
            # conversation_id/message_id 必带：多 Agent 流里外层入口与编排器各发一个
            # start，本事件若缺 conversation_id，体验分享前端（streamHandlers start
            # 分支）会把已捕获的会话 ID 覆盖掉，轮末 setConversationId 不执行 →
            # 下一轮 conversation_id=null → 后端每轮新建会话（"一次输入一个会话"）。
            yield self._format_sse_event("start", {
                "mode": self._normalized_mode,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "message_id": str(message_id) if message_id else None,
                "timestamp": time.time()
            })

            # 2. 根据模式执行（流式）
            # Collaboration 模式：Agent 之间可以相互 handoff（使用 handoffs_service）
            if self._normalized_mode == OrchestrationMode.COLLABORATION:
                async for event in self._execute_collaboration_mode_stream(
                    rendered_message,
                    conversation_id,
                    user_id,
                    web_search,
                    memory,
                    storage_type,
                    user_rag_memory_id,
                    variables=cluster_variables.values or None,
                ):
                    yield event
            # Supervisor 模式（三段式，S9 恢复原功能）：
            # 路由决策（MasterAgentRouter）→ 集合执行 → 末端整合。
            # result_merge_mode / aggregation_strategy / merge_max_tokens 全量生效。
            # S8 期间这里曾被 supervisor_loop 开关顶掉（默认走 ReAct 循环），
            # S9 已把循环上提为独立模式（见下方 SUPERVISOR_LOOP 分支），
            # 本分支不再有任何分流——supervisor 的语义对用户可预期。
            elif self._normalized_mode == OrchestrationMode.SUPERVISOR:
                # variables 进 initial_context，随后作为子 Agent 的 variables 下发
                #（AgentRunService 用它渲染子 Agent 的 system_prompt）。
                # 传副本：顺序/并行协作会往 initial_context 里追加 result_from_* ，
                # 不应反向污染本轮变量包（同实例跑多轮时尤其危险）。
                task_analysis = await self._analyze_task(rendered_message, dict(cluster_variables.values))
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
            # Supervisor Loop 模式（S9 新增）：主管=ReAct 引擎、子 Agent=工具。
            # 分派全权归主管 LLM，可自答、可多轮追加指派；不走路由、不走整合
            #（final 即最终答案）。**不回退三段式**——凭据缺失/无子 Agent/无正文
            # 由循环内部抛 BusinessException，本方法末尾的 except 统一收尾
            #（_finalize_master_execution(failed) + error 事件），错误可归因。
            elif self._normalized_mode == OrchestrationMode.SUPERVISOR_LOOP:
                async for event in self._execute_supervisor_loop_stream(
                    rendered_message,
                    conversation_id,
                    user_id,
                    web_search,
                    memory,
                    storage_type,
                    user_rag_memory_id,
                    files=files,
                ):
                    yield event
            else:
                raise BusinessException(
                    f"不支持的编排模式: {self._normalized_mode}",
                    BizCode.INVALID_PARAMETER
                )

            elapsed_time = time.time() - start_time

            # 发送结束事件
            # conversation_id/message_id 必带：与单 Agent 应用对齐（其 end 事件
            # 携带二者，前端据此回填会话与消息 ID）。多 Agent 缺失时，前端在
            # "仅靠 start 捕获"的路径上一旦漏收，下一轮就会带 null 会话 ID，
            # 后端每轮新建会话（体验分享对话在库里分裂）。
            # merge_mode_actual（S3）：本轮整合实际执行的模式。None 表示该轮没走
            # 到整合阶段（collaboration 等）；"master"=主管 LLM 汇总；"smart"=拼接
            # （配置即拼接、或模型/Key 缺失、整合异常等降级——前端可据此展示
            # "拼接模式"标记，替代此前的静默降级）。
            # usage（S4）：本轮 token 的唯一权威口径（routing/sub/merge 分层 +
            # total）。外层入口（app_chat_service / multi_agent_service）不再拦截
            # sub_usage 字符串自行累加，统一从本事件读取。
            _usage_total = self._turn_routing_tokens + self._turn_sub_tokens + self._turn_merge_tokens
            yield self._format_sse_event("end", {
                "elapsed_time": elapsed_time,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "message_id": str(message_id) if message_id else None,
                "merge_mode_actual": self._merge_mode_actual,
                # S8/S9：主管循环的终止原因（仅 supervisor_loop 模式有值）：
                # direct_answer=主管自答（零 dispatch）；final_after_dispatch=派发后收尾；
                # tool_call_limit/max_iterations=护栏触发；error=异常。
                "loop_stop_reason": self._loop_stop_reason,
                # 对话能力附加字段（execution_mode_actual 等）；无新能力生效时为空，契约不变
                **self._turn_end_extras(),
                "usage": {
                    "routing_tokens": self._turn_routing_tokens,
                    "sub_tokens": self._turn_sub_tokens,
                    "merge_tokens": self._turn_merge_tokens,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": _usage_total,
                },
                "timestamp": time.time()
            })

            logger.info(
                "多 Agent 任务完成（流式）",
                extra={
                    "mode": self._normalized_mode,
                    "elapsed_time": elapsed_time,
                    "routing_tokens": self._turn_routing_tokens,
                    "sub_tokens": self._turn_sub_tokens,
                    "merge_tokens": self._turn_merge_tokens
                }
            )

            await self._finalize_master_execution(
                status="completed",
                elapsed_time=time.time() - start_time,
                token_usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": _usage_total,
                },
            )

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
        message_id: Optional[uuid.UUID] = None,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        thinking: bool = False,
        opening_statement: Optional[str] = None,
        execution_mode: Optional[str] = None,
        files: Optional[List[Any]] = None,
        user_message_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """执行多 Agent 任务（基于 Master Agent 决策）

        Args:
            message: 用户消息
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 变量参数
            use_llm_routing: 是否使用 LLM 路由（保留参数，实际总是使用 Master Agent）
            message_id: 本轮 assistant 消息 ID（写入主执行记录）
            storage_type: 存储类型（S5：透传给 collaboration 调用上下文）
            user_rag_memory_id: 用户 RAG 记忆 ID（S5：同上）

        Returns:
            执行结果
        """
        start_time = time.time()

        # S5：与流式同口径 —— 变量契约在入口一次性处理，两模式共用渲染后的消息。
        cluster_variables = self._build_cluster_variables(variables)
        rendered_message = self._render_entry_message(message, cluster_variables.values)

        logger.info(
            "开始执行多 Agent 任务",
            extra={
                "message_length": len(message),
                "mode": self._normalized_mode,
                "variables": cluster_variables.to_observation(),
            }
        )

        await self._ensure_master_execution(conversation_id, message_id)

        # 对话能力运行态（联网/深度思考/开场白/执行模式）；全部缺省 = 旧行为
        self._prepare_turn_chat_features(
            web_search, thinking, opening_statement, execution_mode, user_message_id
        )

        # S4：每轮重置 token 账本（与流式同口径）
        self._turn_routing_tokens = 0
        self._turn_sub_tokens = 0
        self._turn_merge_tokens = 0

        try:
            # Collaboration 模式：使用 handoffs_service
            if self._normalized_mode == OrchestrationMode.COLLABORATION:
                collab_result = await self._execute_collaboration_mode(
                    rendered_message,
                    conversation_id,
                    user_id,
                    cluster_variables.values or None,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                )
                # S4：collaboration 非流式的 usage 由 handoffs 内部统计口径提供，
                # 汇入账本后统一发布（sub 层）。
                _collab_usage = (collab_result or {}).get("usage") or {}
                self._turn_sub_tokens += int(_collab_usage.get("total_tokens") or 0)
                _collab_total = self._turn_routing_tokens + self._turn_sub_tokens + self._turn_merge_tokens
                await self._finalize_master_execution(
                    status="completed",
                    elapsed_time=time.time() - start_time,
                    token_usage={
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": _collab_total,
                    },
                )
                if collab_result and "usage" in collab_result:
                    collab_result["usage"] = {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": _collab_total,
                    }
                return collab_result

            # Supervisor Loop 模式（S9 新增）：主管=ReAct 引擎、子 Agent=工具。
            # 不走路由、不走整合（final 即最终答案）；失败直接抛错，不回退三段式。
            if self._normalized_mode == OrchestrationMode.SUPERVISOR_LOOP:
                loop_result = await self._execute_supervisor_loop(
                    rendered_message,
                    conversation_id,
                    user_id,
                    storage_type,
                    user_rag_memory_id,
                    web_search=web_search,
                    files=files,
                )
                elapsed_time = time.time() - start_time
                total_tokens = (
                    self._turn_routing_tokens
                    + self._turn_sub_tokens
                    + self._turn_merge_tokens
                )
                await self._finalize_master_execution(
                    status="completed",
                    elapsed_time=elapsed_time,
                    token_usage={
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": total_tokens,
                    },
                )
                return {
                    "message": loop_result.get("content", ""),
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "mode": OrchestrationMode.SUPERVISOR_LOOP,
                    "elapsed_time": elapsed_time,
                    "strategy": "loop",
                    "loop_stop_reason": self._loop_stop_reason,
                    "sub_results": [],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": total_tokens,
                    },
                    # 建议问题 / 引用 / 语音：仅对应 features 开启时才有键，老集群返回不变
                    **(self._turn_extras or {}),
                }

            # Supervisor 模式（三段式，S9 恢复原功能）：路由→集合执行→整合。
            # S8 期间这里曾被 supervisor_loop 开关顶掉，S9 已上提为独立模式，
            # 本路径不再有任何分流。
            # 1. Master Agent 分析任务并做出决策
            task_analysis = await self._analyze_task(rendered_message, dict(cluster_variables.values))

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

            # 4. 汇总 token（S4：改读 per-turn 账本 —— routing/sub/merge 三层
            # 分别在 _analyze_task / _execute_sub_agent(_stream) / merge 内记账，
            # 此处不再从 results 手工提取（旧口径漏 collaboration 路径且会把
            # 已进账本的 sub usage 再加一遍，双计数根因之一）。
            # 不再提取/回传子 Agent 的 conversation_id（S3：那是子 Agent 草稿会话
            # 的 ID，多轮对话的会话归属只认入参 conversation_id——调用方把它原样
            # 透传给下一轮；返回子会话 ID 会把后续轮次引到别的 conversation）。
            total_tokens = self._turn_routing_tokens + self._turn_sub_tokens + self._turn_merge_tokens

            logger.info(
                "多 Agent 任务完成",
                extra={
                    "strategy": routing_decision.get("collaboration_strategy", "single"),
                    "elapsed_time": elapsed_time
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
                # S3：会话 ID 归属集群会话（入参透传），不再是子 Agent 草稿会话
                "conversation_id": str(conversation_id) if conversation_id else None,
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
        # P0-1：conversation_id 从实例属性取（execute/execute_stream 入口的
        # _ensure_master_execution 已写入），路由状态因此能跨轮累积——
        # 此前恒传 None，"当前 Agent / 连续轮数 / switch_count" 全部失真。
        routing_decision = await self.router.route(
            message=message,
            conversation_id=(
                str(self.current_conversation_id) if self.current_conversation_id else None
            ),
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
            "sub_agents": self._effective_sub_agent_entries or self.config.sub_agents,
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

        # P0-4：并发上限走信号量（`_gather_limited`），不再分批 barrier——
        # 分批会让第二批干等第一批最慢的那个，信号量则是"谁先完成谁让位"。
        batch_results = await self._gather_limited(
            [task for _, _, task in tasks]
        )

        results = []
        for (agent_id, agent_name, _), result in zip(tasks, batch_results, strict=False):
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
        execution_mode = self._execution_config.get("sub_agent_execution_mode", "parallel")

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
        merge_mode = self._execution_config.get("result_merge_mode", "master")
        # 智能判断是否需要整合
        need_merge = self._should_merge_results(results, collaboration_strategy)

        if not need_merge:
            # 不需要 LLM 整合：用户已经在各自区块看到全部输出，
            # 这里只把汇总内容按块补进主气泡（不调用模型）
            logger.info("跳过 Master 整合阶段（直接汇总各 Agent 输出）")
            self._set_merge_mode_actual("smart", "need_merge=False（单结果/策略判定不合并）")
            async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                yield event
        elif merge_mode == "master" and len(results) > 1:
            # Master Agent 流式整合
            logger.info("开始 Master Agent 流式整合")
            self._set_merge_mode_actual("master")

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
                self._set_merge_mode_actual("smart", f"master 整合异常: {str(e)[:200]}")
                async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                    yield event
        else:
            # Smart 模式：不调用模型的快速整合（仍按块流式，避免主气泡一坨出现）
            logger.info("使用 Smart 模式整合")
            self._set_merge_mode_actual(
                "smart",
                "配置为 smart" if merge_mode != "master" else "单结果（len<=1）",
            )

            yield self._format_sse_event("merge_start", {
                "merge_mode": "smart",
                "agent_count": len(results)
            })

            async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                yield event

    # ──────────────────────────────────────────────────────────────────
    # S8/S9：supervisor_loop 模式——主管监督循环（ReAct 化，agents-as-tools）
    #
    # S9 起本段只服务 orchestration_mode="supervisor_loop"：
    # - supervisor（三段式）不再分流到循环，MasterAgentRouter/result_merge_mode/
    #   aggregation_strategy 恢复全量生效；
    # - 循环**不回退**三段式：凭据缺失/无子 Agent/无正文直接抛 BusinessException，
    #   由 execute_stream/execute 的统一收尾落 failed 记录并发 error 事件。
    # ──────────────────────────────────────────────────────────────────

    def _build_supervisor_system_prompt(self) -> str:
        """主管 system prompt：用户自定义正文（主管即 Agent）+ 自动段（名册 + 分派纪律）。

        此前主管 prompt 全量硬编码（裸主管）。现在起：
        - `supervisor_config.system_prompt` 非空时：渲染集群变量 {{var}} 后，
          拼在自动段**之前**（用户意图优先，自动段保证分派纪律不被覆盖）；
        - 留空/未配置：与此前完全一致（纯自动段），存量集群零行为突变。

        分派纪律：派单动机是
        **上下文隔离**而不是能力分工——探索性/大输出的任务派给子 Agent 在隔离
        上下文里跑，结论性小问题自己答，避免主上下文被工具输出淹没。
        """
        supervisor_config = self._supervisor_config_dict()
        custom_prompt = (supervisor_config.get("system_prompt") or "").strip()

        # 变量渲染：与入口消息同款（只替换变量包里确实存在的 {{name}}），
        # 主管与子 Agent 共用同一变量包（S5 契约，不设主管私有变量）。
        _bag = getattr(self, "_cluster_variables", None)
        if custom_prompt and _bag is not None:
            custom_prompt = render_variables_in_text(custom_prompt, dict(_bag.values))

        # 主管技能 prompt 段（_load_supervisor_own_tools 加载技能时写入，
        # 调用顺序在 _build_supervisor_agent 内保证先工具后 prompt）
        skill_section = self._supervisor_skill_prompt_section()
        if skill_section:
            if custom_prompt:
                custom_prompt = f"{custom_prompt}\n\n{skill_section}"
            else:
                custom_prompt = skill_section

        roster = "\n".join(
            f"- {data['info'].get('name', agent_id)}：{_sub_agent_brief(data) or '（无描述）'}"
            for agent_id, data in self.sub_agents.items()
        )

        # 自用工具引导（按 supervisor_config 能力面条件生成）。
        # 根因：模型不会主动调用 prompt 未提及的工具——自动段此前只谈子 Agent
        # 分派，"自己能答就直接作答"进一步压制了自用工具（用户开启记忆开关后
        # 主管仍不调 long_term_memory 即此因）。Agent 应用靠用户自己的 prompt
        # 引导；主管自动段是系统生成的，引导必须由系统补齐。
        _sc = self._supervisor_config_dict()
        own_tool_guidance: List[str] = []
        _memory_cfg = _sc.get("memory")
        if isinstance(_memory_cfg, dict) and _memory_cfg.get("enabled"):
            own_tool_guidance.append(
                "- long_term_memory（长期记忆检索）：用户询问自己的历史、偏好、"
                "背景、过往对话内容时，先调用该工具检索，基于检索结果作答，"
                "检索为空再如实说明，不要杜撰用户信息。"
            )
        if _sc.get("web_search"):
            own_tool_guidance.append(
                "- 联网搜索：问题涉及实时信息（新闻、价格、版本、时效性数据）时调用。"
            )
        _kb_cfg = _sc.get("knowledge_retrieval")
        if isinstance(_kb_cfg, dict) and _kb_cfg:
            own_tool_guidance.append(
                "- 知识库检索：问题可能命中已配置知识库内容时优先调用。"
            )
        if _sc.get("tools"):
            own_tool_guidance.append(
                "- 自定义工具：按各工具描述在适用场景调用。"
            )

        own_tools_section = ""
        if own_tool_guidance:
            own_tools_section = (
                "\n\n你自带以下自用工具（与子 Agent 工具并列，直接调用即可）：\n"
                + "\n".join(own_tool_guidance)
            )

        auto_prompt = (
            "你是多智能体集群的主管（supervisor）。你可以调用子 Agent 工具完成子任务，"
            "每个工具对应一个已发布的专家 Agent 应用。\n\n"
            "可用子 Agent：\n"
            f"{roster}\n"
            f"{own_tools_section}\n\n"
            "工作方式：\n"
            "1. 如果你自己（含自用工具）就能直接回答用户问题，不要调用子 Agent 工具，"
            "直接作答；\n"
            "2. 需要专业处理时，调用对应的子 Agent 工具，并在 task 参数里写清楚"
            "该子 Agent 要执行的具体任务/问题（子 Agent 只能看到这段任务书）；\n"
            "3. 收到子 Agent 返回后，判断信息是否足够：足够则直接给出最终答案，"
            "不足则继续派发（可多次、可换人）；\n"
            "4. 终止条件就是不再调用子 Agent 工具，直接输出最终答案。\n"
            "派单动机（重要）：派单是为了**上下文隔离**——探索性搜索、长文档处理、"
            "代码执行这类会产生大量中间输出的任务，交给子 Agent 在独立上下文里跑，"
            "避免主对话被工具输出淹没；结论性小问题用你自己的工具或知识直接回答。\n"
            "最终答案必须是完整、自洽的中文回复，不要提及调度过程。"
        )
        if not custom_prompt:
            return auto_prompt
        return f"{custom_prompt}\n\n---\n\n{auto_prompt}"

    def _supervisor_skill_prompt_section(self) -> str:
        """主管技能 prompt 段：_load_supervisor_own_tools 加载技能时写入。

        与 Agent 应用同款做法（skill_prompts 拼进 system_prompt）。
        _build_supervisor_agent 的调用顺序保证先加载工具、后拼 prompt。
        """
        prompts = (getattr(self, "_loop_skill_prompts", "") or "").strip()
        return prompts

    def _supervisor_config_dict(self) -> Dict[str, Any]:
        """supervisor_config 安全读取（列可能 NULL / 脏值 / dict 形状）。

        主管即 Agent：supervisor_loop 模式的主管能力面。任何异常形状都
        回落空 dict（= 裸主管现状），不阻断执行——配置面问题不该炸运行时。
        """
        raw = self._supervisor_config_snapshot
        if not isinstance(raw, dict):
            return {}
        return raw

    async def _resolve_supervisor_api_key(self) -> Optional["ModelApiKey"]:
        """主管模型凭据（merge 路径同款桥接；S9 后仅供 supervisor_loop 模式，None=调用方抛错）。"""
        if not self.default_model_config_id:
            return None
        try:
            # ModelApiKeyService 无“返回 None”的 bridge 方法，按会话类型分支
            # （分支同 resolve_runtime_api_key_bridge_or_raise_async 内部实现）
            if isinstance(self.db, AsyncSession):
                api_key_config = await ModelApiKeyService.get_available_api_key_async(
                    self.db,
                    self.default_model_config_id,
                    tenant_id=self.tenant_id,
                )
            else:
                api_key_config = ModelApiKeyService.get_available_api_key(
                    self.db,
                    self.default_model_config_id,
                    tenant_id=self.tenant_id,
                )
            return api_key_config or None
        except Exception as e:
            logger.warning(f"S9 supervisor_loop：主管模型凭据获取失败: {e}")
            return None

    def _resolve_loop_max_iterations(self) -> int:
        """解析 supervisor_loop 模式的**主管循环轮次**上限（execution_config.max_iterations）。

        语义：主管 ReAct 循环最多转几轮，一轮 = 主管一次决策里发出的那批子 Agent 调用
        （同一轮并发派 N 个只算 1 轮）。由 `SubAgentTool._execute` 的轮次闸门软限制：
        达到上限后新一轮的调用不再执行，把限制写进工具返回值，让主管基于已有结果收尾；
        不再换算成 LangGraph recursion_limit。

        - 可转 int 且 ≥1 → 返回该值；
        - 未配置 / 空串 / 脏值（0、负数、非数字）→ 返回 ExecutionConfig.max_iterations 的 schema 默认值（10）。
        该参数已不对用户开放配置，正常情况下恒为默认值。
        """
        from app.schemas.multi_agent_schema import ExecutionConfig

        default = ExecutionConfig.model_fields["max_iterations"].default
        cfg = self._execution_config or {}
        try:
            val = int(cfg.get("max_iterations"))
        except (TypeError, ValueError):
            return default
        return val if val >= 1 else default

    async def _build_supervisor_agent(self, api_key_config: "ModelApiKey"):
        """构造主管 LangChainAgent（工具 = 主管自带工具 + SubAgentTool×N）。

        主管即 Agent：主管工具面与子 Agent 工具合并成同一列表（与
        LangChain create_supervisor(agents, tools=[...]) 同构）。

        护栏全用引擎现成参数（不自研）：
        - tool_call_limit：execution_config.supervisor_max_tool_calls（默认 3）
          —— 同一子 Agent 第 N+1 次调用被拒并提示模型给最终答案（无进展检测）；
        - max_iterations：execution_config.max_iterations ——主管循环轮次上限，由
          SubAgentTool._execute 的轮次闸门软限制（限制写进工具返回值让主管收尾）；
          引擎 recursion_limit 恒传 None，按工具数动态计算，仅作硬截断兜底；
        - GraphRecursionError：引擎内部优雅降级。
        """
        from app.core.agent.langchain_agent import LangChainAgent

        mp = self.model_parameters

        def _get(key: str, default: Any) -> Any:
            if mp is None:
                return default
            val = mp.get(key, default) if isinstance(mp, dict) else getattr(mp, key, default)
            return default if val is None else val

        try:
            tool_call_limit = int(
                self._execution_config.get("supervisor_max_tool_calls", 3)
            )
        except (TypeError, ValueError, AttributeError):
            tool_call_limit = 3

        # max_iterations 语义 = 主管循环轮次上限（一轮 = 主管一次决策发出的那批子 Agent 调用，
        # 同批并发只算 1 轮），由 SubAgentTool._execute 的轮次闸门软限制：达到上限后把限制
        # 写进工具返回值，让主管基于已有结果收尾（与引擎 tool_call_limit 同一机制）。
        # 引擎的 LangGraph recursion_limit 只作兜底：传 None 让引擎按
        # 5 + 工具数 × tool_call_limit × 2 动态计算，该值必然不小于软限制能放行的最大步数，
        # 因此硬截断（GraphRecursionError → 引擎吞成一句通用道歉）不会先于软限制触发。
        loop_max_iterations = None

        tools = []
        self._loop_tool_instances = []
        for agent_id, data in self.sub_agents.items():
            info = data.get("info", {})
            wrapper = SubAgentTool(
                self,
                agent_id,
                info.get("name", agent_id),
                _sub_agent_brief(data),
            )
            self._loop_tool_instances.append(wrapper)
            tools.append(wrapper.build())

        # 主管自带工具面（与 SubAgentTool×N 合并成同一列表）。
        # 复用 Agent 应用的构建管线（AgentRunService 的 load_* 系列方法）——
        # 与单 Agent 应用同款能力（工具/技能/知识库/记忆）。构建失败降级为裸主管
        # （只留 SubAgentTool），不阻断对话——能力面是增量，配置/环境问题不该炸运行时。
        #
        # 边界：主管工具面保持"高层"，用户自行决定配什么；主管暂不支持联网搜索
        # （SupervisorConfig 无 web_search，load_tools_config 固定关）。
        own_tools = await self._load_supervisor_own_tools()
        if own_tools:
            tools = own_tools + tools

        # 情绪感知（features.emotion_reply）：等待识别结果并注入主管提示词；
        # 未开启时 _turn_emotion_detection 为 None，原样返回，老集群不受影响。
        _supervisor_prompt = self._build_supervisor_system_prompt()
        if self._turn_emotion_detection is not None:
            try:
                from app.core.memory.emotion.emotion_resolver import apply_detection

                _supervisor_prompt = await apply_detection(
                    _supervisor_prompt,
                    self._turn_emotion_detection,
                    self._turn_user_message_id,
                    write_cache=self.cluster_memory_enabled(),
                )
            except Exception as e:  # noqa: BLE001 - 情绪感知失败不阻断对话
                logger.warning(f"集群情绪注入失败（已跳过）: {e}")
            finally:
                self._turn_emotion_detection = None

        # 上下文引擎（features.context_engine）：未开启/插件缺失/异常均原样返回提示词，
        # 历史由 _resolve_loop_history 回落到集群 20 轮历史，老集群行为不变。
        _supervisor_prompt = await self._prepare_context_engine(
            getattr(self, "current_conversation_id", None), _supervisor_prompt, api_key_config
        )

        # 远端模式（G3）：主管与单 Agent 应用同源——只带非解密模型视图，凭据解密/选路/
        # 换渠道全在模型服务。api_key_config 仍保留给多模态/TTS 等既有凭据消费者，不再进引擎。
        # 调用方已保证 default_model_config_id 存在（_resolve_supervisor_api_key 前置校验）。
        model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
            self.db,
            self.default_model_config_id,
            tenant_id=self.tenant_id,
        )
        return LangChainAgent(
            model_name=model_view.model_name,
            model_view=model_view,
            provider=model_view.provider,
            temperature=_get("temperature", 0.7),
            max_tokens=_get("max_tokens", 4096),
            system_prompt=_supervisor_prompt,
            tools=tools,
            streaming=True,
            tool_call_limit=max(1, tool_call_limit),
            # S10：显式配置则覆盖引擎动态值；None=引擎按工具数动态算
            max_iterations=loop_max_iterations,
            # 深度思考：model_parameters.deep_thinking 且请求 thinking 同时为真才开
            #（_prepare_turn_chat_features 解析）；缺省 False/None = 与改动前一致。
            # json_output：model_parameters.json_output（前端模型配置开关，模型需支持 json_output 能力，
            # 引擎侧会按模型能力归一化）；缺省 False = 与改动前一致。
            json_output=bool(_get("json_output", False)),
            deep_thinking=bool(getattr(self, "_loop_deep_thinking", False)),
            thinking_budget_tokens=(
                _get("thinking_budget_tokens", None)
                if getattr(self, "_loop_deep_thinking", False)
                else None
            ),
        )

    async def _load_supervisor_own_tools(self) -> list:
        """加载主管自带工具面（tools/skills/knowledge/memory；不含联网搜索）。

        复用 AgentRunService 的构建管线（load_tools_config / load_skill_config /
        load_knowledge_retrieval_config）——与单 Agent 应用同源，能力形状零分叉。
        主管技能 prompt（skill_prompts）拼进主管 system_prompt（Agent 应用同款做法）。

        失败语义：任何一段构建失败只记 warning 并跳过该段（工具面降级），
        不抛错——主管工具是增量能力，环境问题（租户无工具/知识库已删）不该
        让集群对话直接不可用。

        Returns:
            list：LangChain 工具列表（可能为空 = 裸主管，仅 SubAgentTool）。
        """
        supervisor_config = self._supervisor_config_dict()
        if not supervisor_config:
            return []

        # 主管工具的运行身份：集群运行用户（无则 "anonymous"，与子 Agent 工具
        # 挂载的 runtime_context 口径一致）。
        user_id = getattr(self, "_loop_user_id", None) or "anonymous"
        conversation_id = getattr(self, "current_conversation_id", None)

        from app.services.draft_run_service import AgentRunService

        run_svc = AgentRunService(self.db)
        tools: list = []

        # 1. 普通工具 + 主管联网（features.web_search.enabled 且请求 web_search 才开；
        #    存量集群无 features → False，与改动前一致）
        try:
            tools_config = supervisor_config.get("tools") or []
            _sup_web = bool(getattr(self, "_loop_supervisor_web_search", False))
            if tools_config or _sup_web:
                own = await run_svc.load_tools_config(
                    tools_config,
                    _sup_web,
                    self.tenant_id,
                    user_id=user_id,
                    workspace_id=await self._cluster_workspace_id(),
                )
                tools.extend(own)
        except Exception as e:
            logger.warning(f"主管工具加载失败（已跳过普通工具段）: {e}")

        # 2. 技能（工具 + 技能 prompt；prompt 在调用方拼进 system_prompt）
        try:
            skills_config = supervisor_config.get("skills")
            if skills_config and isinstance(skills_config, dict) and skills_config.get("enabled"):
                _message = getattr(self, "_loop_entry_message", "") or ""
                skill_tools, skill_prompts = await run_svc.load_skill_config(
                    skills_config,
                    _message,
                    self.tenant_id,
                    user_id=user_id,
                    workspace_id=await self._cluster_workspace_id(),
                )
                tools.extend(skill_tools)
                if skill_prompts:
                    self._loop_skill_prompts = skill_prompts
        except Exception as e:
            logger.warning(f"主管技能加载失败（已跳过技能段）: {e}")

        # 3. 知识库检索。citations_collector 保存到 self._loop_citations，仅供
        # features.citation 开启时输出主管自己知识库的引用；不聚合子 Agent 的引用
        # （主管只吸收子 Agent 的返回内容，其内部检索不关注）。
        try:
            knowledge_config = supervisor_config.get("knowledge_retrieval")
            if knowledge_config and isinstance(knowledge_config, dict):
                from app.integrations.knowledge.contracts import KnowledgeRetrievalSource

                app_id = self._app_id
                workspace_id = await self._cluster_workspace_id()
                if app_id and workspace_id:
                    kb_tools, _citations = await run_svc.load_knowledge_retrieval_config(
                        knowledge_config,
                        user_id,
                        app_id=uuid.UUID(str(app_id)),
                        workspace_id=uuid.UUID(str(workspace_id)),
                        source=KnowledgeRetrievalSource.AGENT,
                    )
                    tools.extend(kb_tools)
                    # 引用收集器随检索工具调用被就地填充；结束时由 citation 开关过滤输出
                    self._loop_citations = _citations if _citations is not None else []
        except Exception as e:
            logger.warning(f"主管知识库加载失败（已跳过知识库段）: {e}")

        # 4. 长期记忆（memory.enabled）——与 Agent 应用同款记忆工具
        try:
            memory_cfg = supervisor_config.get("memory")
            if isinstance(memory_cfg, dict) and memory_cfg.get("enabled"):
                workspace_id = await self._cluster_workspace_id()
                if workspace_id:
                    memory_tools, _enabled = await run_svc.load_memory_config(
                        memory_cfg,
                        user_id,
                        workspace_id,
                        getattr(self, "_loop_storage_type", "") or "",
                        getattr(self, "_loop_user_rag_memory_id", "") or "",
                    )
                    tools.extend(memory_tools)
        except Exception as e:
            logger.warning(f"主管长期记忆加载失败（已跳过记忆段）: {e}")

        # 工具运行时上下文挂载（与 Agent 应用同款：user/conversation 定位）
        for t in tools:
            if hasattr(t, "tool_instance") and hasattr(t.tool_instance, "set_runtime_context"):
                t.tool_instance.set_runtime_context(
                    user_id=user_id,
                    conversation_id=str(conversation_id) if conversation_id else None,
                )

        if tools:
            logger.info(
                "主管自带工具已挂载",
                extra={"tool_count": len(tools), "tenant_id": str(self.tenant_id or "")},
            )
        return tools

    async def _cluster_workspace_id(self) -> Optional[uuid.UUID]:
        """集群所属 workspace（主管工具运行时上下文用）。

        走 _db_get（同步/异步 Session 双兼容，必须 await）。取不到返回 None——
        load_* 管线对 None workspace 自行兜底；但知识库/记忆段在 workspace 缺失时
        会整段跳过，所以读取失败要留 warning，不能静默。
        """
        app_id = self._app_id
        if app_id and self.db is not None:
            try:
                from app.models import App

                app = await self._db_get(App, app_id)
                if app is not None and app.workspace_id:
                    return app.workspace_id
            except Exception as e:
                logger.warning(f"主管工具：读取集群 workspace 失败（忽略）: {e}")
        return None

    def cluster_memory_enabled(self) -> bool:
        """集群记忆开关（长期记忆）——唯一来源：supervisor_config.memory.enabled。

        所有编排模式、所有子 Agent 执行路径、以及本轮消息是否写入记忆
        （should_memorize）都以此为准，**不再受请求级 memory 参数影响**：
        集群开 → 子 Agent 才可能挂记忆（子 Agent 自身 memory.enabled 仍需开启）；
        集群关（含未配置/脏值）→ 一律没有记忆。

        集群会话历史（_load_cluster_history 20 轮）不受此开关控制，始终保留
        ——这是集群会话语义，不是能力开关。
        """
        memory_cfg = self._supervisor_config_dict().get("memory")
        return isinstance(memory_cfg, dict) and bool(memory_cfg.get("enabled"))

    async def _load_cluster_history(
        self,
        conversation_id: Optional[uuid.UUID],
    ) -> List[Dict[str, Any]]:
        """集群会话历史（主管多轮记忆）。

        loop 档下主管每轮带集群会话 messages（排除当前轮正在生成的 assistant
        消息——消息落库走 BatchPersistQueue，此刻还没落）。读失败降级为空
        列表（对话不中断）。
        """
        if not conversation_id or self.db is None:
            return []
        try:
            from app.services.conversation_service import ConversationService

            conv_service = ConversationService(self.db)
            history = await conv_service.get_conversation_history(
                conversation_id, max_history=20,
            )
            # 只保留纯文本轮（多模态 content 是 list，主管链路不支持）
            return [
                {"role": h["role"], "content": h["content"]}
                for h in (history or [])
                if isinstance(h.get("content"), str)
            ]
        except Exception as e:
            logger.warning(f"S8 loop：读取集群会话历史失败（降级为空）: {e}")
            return []

    async def _execute_supervisor_loop_stream(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        web_search: bool = False,
        memory: bool = True,
        storage_type: str = '',
        user_rag_memory_id: str = '',
        files: Optional[List[Any]] = None,
    ):
        """S8/S9 主管监督循环（流式）：主管 ReAct 引擎 + SubAgentTool 桥接。

        仅 orchestration_mode="supervisor_loop" 调用（S9：从 supervisor 内部
        开关上提为独立模式）。

        事件面（前端 clusterStream.ts 零改动）：
        - 子 Agent：agent_dispatch / agent_log / agent_complete（经 event_sink 队列回推）；
        - 主管正文 chunk → message（主气泡逐 token 增长）；
        - 主管 token → _turn_routing_tokens（S4 账本口径，end 事件统一发布）。

        并发结构（关键）：主管引擎的 astream_events 与"子 Agent 工具执行"跑在同一个
        事件循环里——工具执行期间引擎是挂起的。若只在引擎 yield 的间隙排空子 Agent
        事件队列，前端要等子 Agent 整段跑完才看到 dispatch/log/complete，运行面板
        就谈不上"运行"。因此把主管流放进独立 task，与子 Agent 事件泵一起汇入同一个
        merged 队列，本生成器只做"顺序消费 + 分类下发"。

        失败语义（S9 修订，**不回退三段式**）：凭据缺失 / 无子 Agent / 异常且无正文 /
        收尾零正文 → 抛 BusinessException，由 execute_stream 的统一 except 收尾
        （落 failed 执行记录 + error 事件）。已有部分正文的异常保留输出不抛错。
        """
        # 子 Agent 执行参数（SubAgentTool 执行体读取）
        self._loop_user_id = user_id
        # 子 Agent 的联网搜索：纯请求级透传（体验分享入口的会话级开关）。
        # 主管本体不提供搜索配置（SupervisorConfig 无 web_search）
        self._loop_web_search = bool(web_search)
        self._loop_memory = self.cluster_memory_enabled()
        self._loop_storage_type = storage_type
        self._loop_user_rag_memory_id = user_rag_memory_id
        self._loop_stop_reason = None
        # 本轮入口消息（主管技能过滤 load_skill_config 用，同 Agent 应用口径）
        self._loop_entry_message = message

        # S9：前置校验失败直接抛错（模式语义可预期，不静默换档）
        if not self.sub_agents:
            self._loop_stop_reason = "error"
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)
        api_key_config = await self._resolve_supervisor_api_key()
        if api_key_config is None:
            self._loop_stop_reason = "error"
            raise BusinessException(
                "主管循环模式需要可用的主模型凭据（默认模型未配置或 API Key 不可用）",
                BizCode.AGENT_CONFIG_MISSING,
            )

        self._set_merge_mode_actual("loop", "supervisor_loop 模式（主管 ReAct 循环）")

        # 情绪感知：features.emotion_reply 开启才起后台识别任务（与构建主管并发），
        # 结果在 _build_supervisor_agent 里注入主管提示词；关闭时为 None，零开销。
        self._start_turn_emotion_detection(message)
        supervisor = await self._build_supervisor_agent(api_key_config)
        # 历史：context_engine 本轮生效则用其结果，否则沿用集群 20 轮历史；再拼开场白（无开场白原样返回）
        history = await self._resolve_loop_history(conversation_id)
        # 文件上传（features.file_upload）：无 files 时 llm_message=message、processed_files=None，与改动前一致
        llm_message, processed_files = await self._prepare_loop_files(
            files, api_key_config, message, supervisor
        )

        # 子 Agent 可观测事件队列：SubAgentTool 执行体 → 主管循环 → SSE。
        # 挂接走 _loop_tool_instances 实例列表（_build_supervisor_agent 填充），
        # 不从 LangChain 工具对象反查——引擎 _wrap_tools_with_call_limit 会就地
        # 包装 _run/_arun，从工具对象反查闭包 self 不可靠。
        event_queue: asyncio.Queue = asyncio.Queue()
        self._loop_iteration_limit_hit = False
        self._loop_rounds = 0
        self._loop_inflight = 0
        for wrapper in getattr(self, "_loop_tool_instances", []) or []:
            wrapper.event_sink = event_queue
            wrapper.calls = 0

        merged: asyncio.Queue = asyncio.Queue()
        _DONE = object()

        async def _produce_supervisor():
            """主管引擎流 → merged 队列（异常转成 error 标记，不让它炸在 task 里）。"""
            try:
                async for item in supervisor.chat_stream(
                    message=llm_message, history=history or None, files=processed_files or None
                ):
                    await merged.put(item)
            except Exception as exc:  # noqa: BLE001 - 统一交给下方降级判定
                await merged.put(("error", exc))
            finally:
                await merged.put(_DONE)

        async def _pump_sub_events():
            """子 Agent 事件泵：工具执行体随时投递，这里立刻转进 merged 队列。"""
            while True:
                sse_event = await event_queue.get()
                if sse_event:
                    await merged.put(sse_event)

        producer = asyncio.ensure_future(_produce_supervisor())
        pump = asyncio.ensure_future(_pump_sub_events())

        final_content = ""
        supervisor_tokens = 0
        loop_error: Optional[BaseException] = None

        # 流式 TTS（features.text_to_speech 开启才建；关闭时三者均为 None，零开销）。
        # 只喂主管最终正文 chunk，不喂子 Agent 内容。
        _api_key_dict = self._supervisor_api_key_dict(api_key_config)
        tts_queue, tts_audio_url, tts_task = await self._setup_turn_tts(_api_key_dict)

        try:
            while True:
                item = await merged.get()
                if item is _DONE:
                    break
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "error":
                    loop_error = item[1]
                    break
                # 子 Agent 可观测事件：整条 SSE 字符串原样下发（自带 execution_id 归属）
                if isinstance(item, str) and item.startswith("event:"):
                    yield item
                    continue
                # LangChainAgent.chat_stream 的 yield 类型契约（引擎 :1019 注释）：
                # str=正文 chunk；int=token；dict=agent_log/reasoning/tool_*；
                # list=node_executions
                if isinstance(item, str):
                    final_content += item
                    if tts_queue is not None:
                        tts_queue.put_nowait(item)
                    yield self._format_sse_event("message", {"content": item})
                elif isinstance(item, int):
                    supervisor_tokens = max(supervisor_tokens, int(item))
                elif isinstance(item, dict):
                    # 主管自有工具调用过程下发（用户口径：运行面板展示
                    # "工具调用与子 Agent 调用"——主管自有工具属于前者）。
                    # 引擎在 chat_stream 里对每次 on_tool_start/tool_end/tool_error
                    # 各 yield 一条 dict（langchain_agent.py:1214/:1288/:1327），
                    # 此前整个 dict 分支只记 token，工具调用在面板上不可见。
                    # SubAgentTool 不转发：子 Agent 调用已有 agent_dispatch/
                    # agent_log/agent_complete 专属区块（事件由工具执行体经
                    # event_sink 回推），这里再转发会造成同一调用展示两份。
                    _item_type = item.get("type")
                    # 深度思考内容：仅在 deep_thinking 生效时引擎才会产出 reasoning；
                    # 老集群（未开启）不会出现该事件，契约不变。格式与 Agent 一致。
                    if _item_type == "reasoning":
                        _reasoning_chunk = item.get("content")
                        if _reasoning_chunk:
                            yield self._format_sse_event("reasoning", {"content": _reasoning_chunk})
                    if _item_type in ("tool_start", "tool_end", "tool_error"):
                        _tool_name = str(item.get("name") or "")
                        _sub_agent_tool_names = {
                            wrapper.tool_name
                            for wrapper in (getattr(self, "_loop_tool_instances", None) or [])
                        }
                        if _tool_name and _tool_name not in _sub_agent_tool_names:
                            payload = {k: v for k, v in item.items() if k != "type"}
                            yield self._format_sse_event(_item_type, payload)
                    # 主管轨迹不进集群 SSE（用户口径：运行面板只展示工具调用与
                    # 子 Agent 调用，主管的调度思考不是"调用"）。token 必须取：
                    # 引擎最终只 yield 最后一次 LLM 调用的 token，trace.finalize
                    # 会覆盖 meta.total_tokens，多轮循环会漏账；iterations[].llm.tokens
                    # 是逐轮累加值，用它对齐 S4 账本。
                    _trace_tokens = _trace_total_tokens(item)
                    if _trace_tokens:
                        supervisor_tokens = max(supervisor_tokens, _trace_tokens)
                # list（node_executions）忽略：步骤归属由执行记录承载
        finally:
            pump.cancel()
            if not producer.done():
                producer.cancel()
            else:
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass

        # 排空子 Agent 队列残余（含工具兜底发出的 failed 收尾），保证前端区块
        # 不会永远停在 running。放在 try 之外：yield 写在 finally 里遇上消费方
        # 提前断开（GeneratorExit）会抛 "async generator ignored GeneratorExit"。
        while not event_queue.empty():
            try:
                _pending = event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if _pending:
                yield _pending

        self._loop_stop_reason = self._resolve_loop_stop_reason(loop_error)

        # 主管 token 记账（S4：routing 层 = 主管决策与生成；sub 层 = 子 Agent）
        self._turn_routing_tokens += int(supervisor_tokens or 0)

        # 正文结束：通知 TTS 队列收尾，并产出 end 附加字段（建议问题 / 引用 / 语音）。
        # 各项仅在对应 features 开启时才有值；无正文（将抛错）时不生成。
        if tts_queue is not None:
            tts_queue.put_nowait(None)
        if final_content.strip():
            await self._finalize_turn_extras(
                final_content, _api_key_dict, audio_url=tts_audio_url, tts_task=tts_task
            )

        if loop_error is not None:
            logger.error(
                "S9 supervisor_loop：主管循环执行失败",
                extra={"error": str(loop_error), "stop_reason": self._loop_stop_reason},
                exc_info=loop_error,
            )
            if final_content:
                # 已有部分输出：保留给用户，不抛错也不重跑（重复消耗 + 答案分叉）。
                # 前端已收到正文，收尾照常走 end 事件。
                self._set_merge_mode_actual(
                    "loop", f"loop 异常（保留部分输出）: {str(loop_error)[:200]}"
                )
                return
            # S9：零输出异常直接抛错（不回退三段式——模式语义可预期，错误可归因）
            self._set_merge_mode_actual("loop", f"loop 异常: {str(loop_error)[:200]}")
            raise BusinessException(
                f"主管循环执行失败: {str(loop_error)[:200]}",
                BizCode.LLM_ERROR,
            )

        # 引擎正常结束却一个字都没产出（模型只调工具不收尾 / GraphRecursionError 后
        # 引擎吞掉输出）：S9 起不再交给三段式兜底——空答案对用户是静默失败，
        # 抛错让前端与执行记录都能归因。
        if not final_content.strip():
            logger.warning("S9 supervisor_loop：主管未产出正文")
            self._set_merge_mode_actual("loop", "loop 无正文产出")
            raise BusinessException(
                "主管循环未产出任何回答（可能已达分派/轮数护栏仍未收尾），请重试或调整"
                " supervisor_max_tool_calls；需要确定性编排可改用主管模式（三段式）",
                BizCode.LLM_ERROR,
            )

    def _resolve_loop_stop_reason(self, loop_error: Optional[BaseException]) -> str:
        """判定本轮循环终止原因（end 事件 loop_stop_reason 字段，护栏可观测）。

        - direct_answer：主管自答（零 dispatch）——S8 的核心收益之一；
        - final_after_dispatch：派发后正常收尾；
        - tool_call_limit：某子 Agent 达到 supervisor_max_tool_calls 上限被拒（无进展检测）；
        - max_iterations：主管循环轮次达到 max_iterations（轮次闸门，见 SubAgentTool._execute），
          或引擎 GraphRecursionError（硬截断兜底）；
        - error：其它异常。
        """
        if loop_error is not None:
            return "max_iterations" if type(loop_error).__name__ == "GraphRecursionError" else "error"
        if self._loop_iteration_limit_hit:
            return "max_iterations"

        wrappers = list(getattr(self, "_loop_tool_instances", []) or [])
        total_calls = sum(int(getattr(w, "calls", 0) or 0) for w in wrappers)
        if total_calls == 0:
            return "direct_answer"
        try:
            limit = int(self._execution_config.get("supervisor_max_tool_calls", 3))
        except (TypeError, ValueError, AttributeError):
            limit = 3
        if any(int(getattr(w, "calls", 0) or 0) >= max(1, limit) for w in wrappers):
            return "tool_call_limit"
        return "final_after_dispatch"

    async def _execute_supervisor_loop(
        self,
        message: str,
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        storage_type: str = '',
        user_rag_memory_id: str = '',
        web_search: bool = False,
        files: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """S8/S9 主管监督循环（非流式）：语义与流式版一致（引擎 chat()）。

        仅 orchestration_mode="supervisor_loop" 调用（S9）。**不回退三段式**：
        凭据缺失 / 无子 Agent / 无正文 / 异常 → 抛 BusinessException，由
        execute() 的统一 except 收尾（落 failed 执行记录）。

        Returns:
            dict：{content, usage, loop_stop_reason}（loop 正常完成）。
        """
        self._loop_user_id = user_id
        # 子 Agent 联网：请求级透传（web_search 缺省 False，老调用方行为不变）
        self._loop_web_search = bool(web_search)
        self._loop_memory = self.cluster_memory_enabled()
        self._loop_storage_type = storage_type
        self._loop_user_rag_memory_id = user_rag_memory_id
        self._loop_stop_reason = None
        self._loop_entry_message = message

        # S9：前置校验失败直接抛错
        if not self.sub_agents:
            self._loop_stop_reason = "error"
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)
        api_key_config = await self._resolve_supervisor_api_key()
        if api_key_config is None:
            self._loop_stop_reason = "error"
            raise BusinessException(
                "主管循环模式需要可用的主模型凭据（默认模型未配置或 API Key 不可用）",
                BizCode.AGENT_CONFIG_MISSING,
            )

        self._set_merge_mode_actual("loop", "supervisor_loop 模式（主管 ReAct 循环）")

        # 情绪感知：features.emotion_reply 开启才起后台识别任务；关闭时为 None，零开销
        self._start_turn_emotion_detection(message)
        supervisor = await self._build_supervisor_agent(api_key_config)
        # 历史：context_engine 本轮生效则用其结果，否则沿用集群 20 轮历史；再拼开场白
        history = await self._resolve_loop_history(conversation_id)
        # 文件上传（features.file_upload）：无 files 时 llm_message=message、processed_files=None，与改动前一致
        llm_message, processed_files = await self._prepare_loop_files(
            files, api_key_config, message, supervisor
        )

        # 非流式 loop：sink 不挂接（无 SSE 出口），子 Agent 事件自然丢弃；
        # S2 执行树仍由 run_stream 内部落库，日志视图不缺数据。
        # 调用次数照旧统计（loop_stop_reason 判定需要）。
        self._loop_iteration_limit_hit = False
        self._loop_rounds = 0
        self._loop_inflight = 0
        for wrapper in getattr(self, "_loop_tool_instances", []) or []:
            wrapper.calls = 0
            wrapper.event_sink = None
        try:
            result = await supervisor.chat(
                message=llm_message, history=history or None, files=processed_files or None
            )
            content = (result or {}).get("content", "")
            # 非流式的 usage 同样只含末次 LLM 调用（引擎口径），多轮会漏；
            # 流式侧用 trace 逐轮累加修正，这里引擎不吐 trace，保持末次口径
            # 并在日志留痕（账本口径与流式存在差异）。
            total_tokens = int(((result or {}).get("usage") or {}).get("total_tokens") or 0)
            self._turn_routing_tokens += total_tokens
            self._loop_stop_reason = self._resolve_loop_stop_reason(None)
            if not (content or "").strip():
                logger.warning("S9 supervisor_loop（非流式）：主管未产出正文")
                self._set_merge_mode_actual("loop", "loop 无正文产出")
                raise BusinessException(
                    "主管循环未产出任何回答（可能已达分派/轮数护栏仍未收尾），请重试或调整"
                    " supervisor_max_tool_calls；需要确定性编排可改用主管模式（三段式）",
                    BizCode.LLM_ERROR,
                )
            # 建议问题 / 引用 / TTS：仅对应 features 开启时才产生（缺省关闭 = 无新键）
            await self._finalize_turn_extras(
                content,
                self._supervisor_api_key_dict(api_key_config),
                non_stream_tts=True,
            )
            return {
                "content": content,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": total_tokens,
                },
                "loop_stop_reason": self._loop_stop_reason,
            }
        except BusinessException:
            # 业务错误（含上面的"无正文"）直接上抛，由 execute() 统一收尾
            raise
        except Exception as e:
            logger.error(
                "S9 supervisor_loop（非流式）：主管循环执行失败",
                extra={"error": str(e)},
                exc_info=True,
            )
            self._loop_stop_reason = (
                "max_iterations" if type(e).__name__ == "GraphRecursionError" else "error"
            )
            self._set_merge_mode_actual("loop", f"loop 异常: {str(e)[:200]}")
            raise BusinessException(
                f"主管循环执行失败: {str(e)[:200]}",
                BizCode.LLM_ERROR,
            )

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

        # S9 说明：本方法只承载 supervisor 三段式（supervisor_loop 模式在
        # execute_stream 的独立分支走 ReAct 循环，不经过这里）。

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
        # S4：路由 token 进账本（_analyze_task 已从 router._last_routing_tokens
        # 取出），不再以 sub_usage 事件发给上层 —— 账本在 end 事件统一发布，
        # 上层不再解析 SSE 字符串累加。
        self._turn_routing_tokens += int(task_analysis.get("routing_tokens", 0) or 0)

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
            # 子运行生命周期事件（start/end）不透传：否则上层会看到两个 start / 两个 end，
            # 且子运行的临时 message_id 会顶掉集群气泡的真实 id（详见 _SUB_RUN_LIFECYCLE_EVENTS）。
            if _sse_event_name(event) in _SUB_RUN_LIFECYCLE_EVENTS:
                continue

            # 其余事件直接透传；sub_usage 已在 _execute_sub_agent_stream 内记入
            # 账本不再透传（S4）；
            # 先剥离子运行自己的 conversation_id（S3：子 Agent 草稿会话 ID 会污染
            # 前端会话指针，导致后续轮次打到别的 conversation），
            # 再补上 Agent 归属（只补缺失字段，不覆盖事件自带同名字段），
            # 供运行中面板把事件分流到对应的子 Agent 区块。
            yield self._inject_agent_meta(
                self._strip_conversation_id(event),
                self._sub_event_meta(agent_id, agent_info.get("name")),
            )

        # 6. 会话 ID 已由本编排器自己的 start/end 事件以集群会话 ID 唯一发布，
        # 这里不再对外发 conversation 事件（原逻辑转发的是子 Agent 草稿会话 ID，
        # 前端一旦采纳，后续轮次就会写入另一条 conversation —— 会话分裂根因之一）。

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

    # S10：原 `_execute_loop`（迭代优化循环）已删除——全仓零调用者，是早期
    # sequential/parallel/conditional/loop 四模式设计的残留。它同时是
    # `execution_config.max_iterations` 的**唯一**读取点，删掉后该字段改由
    # `_resolve_loop_max_iterations` 接给 supervisor_loop 模式的引擎（见该方法）。

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

    def _parallel_limit(self) -> int:
        """execution_config.parallel_limit（非法值回落 schema 默认 3）。

        schema 已约束 `ge=1`，但存量 JSON / 手工改库可能给出 0、负数、空串。
        这类值回落 3（与 schema 默认一致）而不是 1 —— 1 会把并行悄悄退化成串行，
        性能问题会被误诊为"模型变慢"。
        """
        cfg = self._execution_config or {}
        raw = cfg.get("parallel_limit", None)
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return 3
        return limit if limit >= 1 else 3

    async def _gather_limited(self, coros: List[Any]) -> List[Any]:
        """带并发上限的 gather，语义对齐 `gather(..., return_exceptions=True)`。

        P0-4：替换原来的"分批 barrier"写法。分批的问题是**批内同步**——
        limit=3、5 个子 Agent 时，第二批必须等第一批全部结束才开始，
        整轮耗时 = 最慢批次之和；而信号量是"谁先完成谁让位"，
        整轮耗时 = 最慢单个 + 排队开销。结果顺序与传入顺序一致（与 gather 相同）。
        """
        if not coros:
            return []
        limit = self._parallel_limit()
        if limit >= len(coros):
            return list(await asyncio.gather(*coros, return_exceptions=True))

        sem = asyncio.Semaphore(limit)

        async def _run(coro):
            async with sem:
                return await coro

        return list(
            await asyncio.gather(*[_run(c) for c in coros], return_exceptions=True)
        )

    def _sub_agent_guards(self) -> tuple[float, float, bool, int]:
        """读 execution_config 的超时/重试护栏（P0-4）。

        Returns:
            (total_timeout, stream_idle_timeout, retry_on_failure, max_retries)

        两个超时口径**故意分开**：

        - `total_timeout` = `execution_config.timeout`（默认 60s）——非流式路径的
          **总时长**上限。非流式一次性返回，超时即失败，语义无歧义。
        - `stream_idle_timeout` = `execution_config.stream_idle_timeout`
          （默认 300s）——流式路径的**事件间空闲**上限。

        为什么不把 `timeout` 直接当流式空闲超时：子 Agent 跑工具时（沙箱代码执行、
        联网搜索）`tool_start` 之后到 `tool_end` 之前**完全静默**，合法耗时轻松超过
        60s。用 60s 空闲阈值会杀掉正常工具调用——把一个"卡死"问题换成一个
        "功能被误杀"问题，后者更难排查。300s 仍能兜住真死锁（滚轮永久冻屏），
        又不误伤长工具。<=0 表示不限时。
        """
        cfg = self._execution_config or {}

        def _num(key: str, default: float = 0.0) -> float:
            """取数值配置：缺失 / None / 空串 / 脏值一律回落 default。

            空串必须回落 default 而不是 0——`stream_idle_timeout=''` 若解析成 0，
            等于**静默关掉死锁护栏**（0 在本口径下是"不限时"），而这恰恰是
            存量配置里最可能出现的形态（前端表单清空后存的就是空串）。
            显式的数值 0 则尊重用户意图 = 关闭该护栏。
            """
            raw = cfg.get(key, None)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        total_timeout = max(0.0, _num("timeout", 60.0))
        stream_idle = max(0.0, _num("stream_idle_timeout", 300.0))
        retry_on = bool(cfg.get("retry_on_failure", False))
        max_retries = max(0, int(_num("max_retries", 2)))
        return total_timeout, stream_idle, retry_on, max_retries

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        """只有**瞬时**失败才值得重试。

        不重试的三类：
        1. `BusinessException`——配置缺失/校验失败，重跑必然同样失败，只是白烧配额；
        2. `asyncio.CancelledError`——取消是外部意图，吞掉会破坏优雅关闭；
        3. 超时（`TimeoutError`）——LLM 慢到超时，重试通常只会再等一次超时；
           把已经等了 60s 的请求再排 60s，用户侧观感是彻底卡死。
        """
        if isinstance(exc, (asyncio.CancelledError, TimeoutError, asyncio.TimeoutError)):
            return False
        if isinstance(exc, BusinessException):
            return False
        return True

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
        """执行单个子 Agent（P0-4：超时 + 重试护栏外壳）。

        护栏放在这里而不是 14 个调用点——逐个包裹必然漏改，且各处口径会漂移。
        非流式路径的重试是安全的：结果在完整返回前对调用方不可见，前端不会
        看到重复内容（代价是失败那次会留一条 failed 执行记录，属于期望的可观测）。
        """
        total_timeout, _stream_idle, retry_on, max_retries = self._sub_agent_guards()
        attempts = 1 + (max_retries if retry_on else 0)
        agent_name = getattr(agent_config, "name", None) or str(getattr(agent_config, "id", ""))
        last_exc: Optional[BaseException] = None

        for attempt in range(1, attempts + 1):
            try:
                if total_timeout > 0:
                    return await asyncio.wait_for(
                        self._execute_sub_agent_once(
                            agent_config, message, context, conversation_id, user_id,
                            web_search, memory, storage_type, user_rag_memory_id,
                        ),
                        timeout=total_timeout,
                    )
                return await self._execute_sub_agent_once(
                    agent_config, message, context, conversation_id, user_id,
                    web_search, memory, storage_type, user_rag_memory_id,
                )
            except Exception as e:  # noqa: BLE001 —— 护栏需要看到所有异常再决定
                last_exc = e
                is_timeout = isinstance(e, (TimeoutError, asyncio.TimeoutError))
                retryable = attempt < attempts and self._is_retryable(e)
                logger.log(
                    logging.WARNING if retryable else logging.ERROR,
                    "子 Agent 执行失败，准备重试" if retryable else "子 Agent 执行失败",
                    extra={
                        "agent": agent_name,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "timeout": is_timeout,
                        "timeout_limit": total_timeout,
                        "error": str(e)[:300],
                    },
                )
                if not retryable:
                    raise
        # 理论不可达（循环内必然 return 或 raise）
        if last_exc:
            raise last_exc
        raise RuntimeError("子 Agent 执行未返回结果")

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
        """执行单个子 Agent（流式，P0-4：空闲超时 + 幂等边界内重试）。

        **超时口径**：流式用"事件间空闲超时"（`stream_idle_timeout`，默认 300s）
        而不是 `timeout`（60s）。子 Agent 跑沙箱代码 / 联网搜索时，`tool_start`
        到 `tool_end` 之间完全静默且合法耗时轻松超过 60s——拿 60s 当空闲阈值会
        误杀正常工具调用。300s 仍能兜住真死锁（用户侧观感=滚轮永久冻屏）。

        **重试边界**：只在**一个事件都还没吐出去**之前重试。一旦前端已收到
        dispatch/正文，重跑会让气泡出现重复内容、执行记录出现两条 running，
        这种"半程重试"比不重试更糟。因此实际生效范围是前置失败
        （模型配置读取失败、连接建立失败等），这也正是重试最可能成功的场景。
        """
        _total, idle_timeout, retry_on, max_retries = self._sub_agent_guards()
        attempts = 1 + (max_retries if retry_on else 0)
        agent_name = getattr(agent_config, "name", None) or str(getattr(agent_config, "id", ""))

        for attempt in range(1, attempts + 1):
            emitted = False
            timed_out = False
            agen = None
            try:
                agen = self._execute_sub_agent_stream_once(
                    agent_config, message, context, conversation_id, user_id,
                    web_search, memory, storage_type, user_rag_memory_id,
                )
                ait = agen.__aiter__()
                while True:
                    try:
                        if idle_timeout > 0:
                            event = await asyncio.wait_for(
                                ait.__anext__(), timeout=idle_timeout
                            )
                        else:
                            event = await ait.__anext__()
                    except StopAsyncIteration:
                        break
                    except (TimeoutError, asyncio.TimeoutError):
                        # 空闲超时：底层生成器被 wait_for 取消，不再重试
                        #（已经跑了这么久，重跑只是让用户再等一次）
                        timed_out = True
                        logger.error(
                            "子 Agent 流式空闲超时，中断本次执行",
                            extra={
                                "agent": agent_name,
                                "idle_timeout": idle_timeout,
                                "emitted": emitted,
                            },
                        )
                        raise
                    emitted = True
                    yield event
                return
            except Exception as e:  # noqa: BLE001
                retryable = (
                    not emitted
                    and attempt < attempts
                    and not timed_out
                    and self._is_retryable(e)
                )
                logger.log(
                    logging.WARNING if retryable else logging.ERROR,
                    "子 Agent 流式执行失败，准备重试" if retryable else "子 Agent 流式执行失败",
                    extra={
                        "agent": agent_name,
                        "attempt": attempt,
                        "emitted": emitted,
                        "timeout": timed_out,
                        "error": str(e)[:300],
                    },
                )
                if not retryable:
                    raise
            finally:
                # 重试/异常路径都要关闭生成器，否则底层 run_stream 的
                # async context（DB session 等）不会及时释放
                if agen is not None:
                    try:
                        await agen.aclose()
                    except Exception:  # noqa: BLE001
                        pass

    async def _execute_sub_agent_stream_once(
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
        """执行单个子 Agent（流式）—— 单次尝试的真实实现。

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
        # S4：sub 层 token 在此统一记账（子 Agent 的 sub_usage 事件到这里被
        # 消化进账本后不再透传，外层不再解析 SSE 字符串累加）。
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
            # 记忆开关唯一来源=集群配置（所有编排模式、所有调用路径汇于此处）；
            # 入参 memory 仅为兼容既有签名，不再参与决策
            memory=self.cluster_memory_enabled(),
            sub_agent=True,
            parent_execution_id=self.current_execution_id,
            orchestration_mode=self._normalized_mode,
            execution_owner=self._resolve_agent_identity(agent_config),
        ):
            _name = _sse_event_name(event)
            if _name == "sub_usage":
                try:
                    data_line = event.split("data: ", 1)[1].strip()
                    data = json.loads(data_line)
                    self._turn_sub_tokens += int(data.get("total_tokens") or 0)
                except Exception:
                    pass
                continue
            yield event

    async def _execute_sub_agent_once(
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
        """执行单个子 Agent —— 单次尝试的真实实现。

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
        # S4：sub 层 token 统一记账（非流式结果里的 usage.total_tokens 进账本）。
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
            # 记忆开关唯一来源=集群配置（与流式路径一致）
            memory=self.cluster_memory_enabled(),
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            sub_agent=True,
            parent_execution_id=self.current_execution_id,
            orchestration_mode=self._normalized_mode,
            execution_owner=self._resolve_agent_identity(agent_config),
        )

        _usage = (result or {}).get("usage") or {}
        self._turn_sub_tokens += int(_usage.get("total_tokens") or 0)

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
        user_rag_memory_id: str = '',
        variables: Optional[Dict[str, Any]] = None,
    ):
        """Collaboration 模式流式执行 - Agent 之间可以相互 handoff

        使用 handoffs_service 实现 Agent 之间的动态切换

        Args:
            message: 用户消息（入口已按变量契约渲染）
            conversation_id: 会话 ID
            user_id: 用户 ID
            web_search: 是否启用网络搜索
            memory: 是否启用记忆
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆 ID
            variables: 集群变量值包（S5）。此前这些参数在调用 handoffs 时被全部丢弃，
                协作模式因此没有变量渲染、没有任何调用上下文。现在一并透传：
                变量进节点渲染，其余进 LangChain 调用 metadata（可观测归因）。

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
                "sub_agents": self._effective_sub_agent_entries or self.config.sub_agents,
                "orchestration_mode": self.config.orchestration_mode
            }

            # 2. 转换配置（每个 Agent 包含自己的 model_view）
            agent_configs = await convert_multi_agent_config_to_handoffs(
                multi_agent_config,
                self.db
            )

            if not agent_configs:
                raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

            # 3. 创建 HandoffsService
            # S5：变量包 + 调用上下文（user_id/memory/storage_type/user_rag_memory_id）
            # 在这里一次性注入，节点构建时各 Agent 的 system_prompt 即完成渲染。
            # P0-2：checkpoint 外置到 Redis（探测失败则降级进程内 MemorySaver），
            # thread_prefix 带租户 ID 做隔离。降级决策集中在这里一处，
            # HandoffsService 只负责"给什么用什么"。
            from app.core.agent.redis_checkpoint import create_async_checkpointer
            checkpointer = await create_async_checkpointer(prefix="cluster")
            handoffs_service = HandoffsService(
                agent_configs=agent_configs,
                streaming=True,
                variables=variables or None,
                runtime_context={
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "user_id": user_id,
                    "memory": self.cluster_memory_enabled(),

                    "storage_type": storage_type,
                    "user_rag_memory_id": user_rag_memory_id,
                },
                checkpointer=checkpointer,
                thread_prefix=(
                    str(self.tenant_id) if self.tenant_id else "shared"
                ),
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
                    # S4：token 进 per-turn 账本（sub 层），激活局部 active_tokens
                    # 仅用于该激活的 agent_complete/执行记录；sub_usage 不再外透
                    #（外层不再累加，账本在 end 事件统一发布）。
                    _evt_tokens = int(event_data.get("total_tokens") or 0)
                    active_tokens += _evt_tokens
                    self._turn_sub_tokens += _evt_tokens
                    continue
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
                # 其余事件先剥离子运行 conversation_id（S3：防前端会话指针被
                # handoffs 内部会话 ID 污染——集群会话 ID 只由 start/end 发布）。
                if event_name != "end":
                    yield self._strip_conversation_id(event)

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
        variables: Optional[Dict[str, Any]] = None,
        storage_type: str = '',
        user_rag_memory_id: str = '',
    ) -> Dict[str, Any]:
        """Collaboration 模式非流式执行 - Agent 之间可以相互 handoff

        使用 handoffs_service 实现 Agent 之间的动态切换

        Args:
            message: 用户消息（入口已按变量契约渲染）
            conversation_id: 会话 ID
            user_id: 用户 ID
            variables: 集群变量值包（S5：此前收下后被丢弃，协作模式没有变量渲染）
            storage_type: 存储类型（S5：进调用上下文）
            user_rag_memory_id: 用户 RAG 记忆 ID（S5：同上）

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
                "sub_agents": self._effective_sub_agent_entries or self.config.sub_agents,
                "orchestration_mode": self.config.orchestration_mode
            }

            # 2. 转换配置（每个 Agent 包含自己的 model_view）
            agent_configs = await convert_multi_agent_config_to_handoffs(
                multi_agent_config,
                self.db
            )

            if not agent_configs:
                raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

            # 3. 创建 HandoffsService
            # S5：与流式同口径 —— 变量包进节点渲染，调用上下文进 LLM metadata。
            # P0-2：与流式同口径 —— Redis checkpoint + 租户前缀 thread_id。
            from app.core.agent.redis_checkpoint import create_async_checkpointer
            checkpointer = await create_async_checkpointer(prefix="cluster")
            handoffs_service = HandoffsService(
                agent_configs=agent_configs,
                streaming=False,
                variables=variables or None,
                runtime_context={
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "user_id": user_id,
                    "storage_type": storage_type,
                    "user_rag_memory_id": user_rag_memory_id,
                },
                checkpointer=checkpointer,
                thread_prefix=(
                    str(self.tenant_id) if self.tenant_id else "shared"
                ),
            )

            # 4. 使用 handoffs_service 的非流式聊天
            conv_id = str(conversation_id) if conversation_id else None

            result = await handoffs_service.chat(
                message=message,
                conversation_id=conv_id
            )

            elapsed_time = time.time() - start_time

            # 子执行记录（与流式路径的树结构对齐）：
            # 非流式拿不到逐激活的实时事件，退化为"运行结束按激活历史补记"——
            # handoff_history 是本次运行依次激活过的 Agent（首 Agent 是
            # create_route_initial 的默认/active，其后是每个发起 handoff 的 Agent
            # ——目标 Agent 由后继激活承接，顺序与真实执行一致）。
            # 每条记录：parent=master、conversation_id=集群会话、status=completed。
            # 观测降级不阻断对话（与 _ensure_master_execution 同风格）。
            try:
                # 零 handoff 时 handoff_history 为空、active_agent 也可能为 None，
                # 兜底用默认 Agent（create_route_initial 的首选 = agent_configs 首键）
                activated = list(result.get("handoff_history") or [])
                if result.get("active_agent") and result.get("active_agent") not in activated:
                    activated.append(result.get("active_agent"))
                if not activated and agent_configs:
                    activated = [next(iter(agent_configs))]
                usage = result.get("usage") or {}
                per_activation = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": usage.get("total_tokens", 0),
                }
                for i, agent_key in enumerate(activated):
                    info = agent_configs.get(agent_key) or {}
                    steps = [{
                        "step_id": f"collab_{i}_{agent_key}",
                        "node_type": "agent",
                        "node_name": info.get("name") or agent_key,
                        "status": "completed",
                        "input": message if i == 0 else f"(handoff 接收) {message}",
                        "output": (result.get("response", "") if i == len(activated) - 1 else "")[:2000],
                    }]
                    await self._open_and_close_collaboration_activation(
                        agent_configs, agent_key, info.get("name"), message,
                        status="completed",
                        elapsed_time=elapsed_time if i == 0 else None,
                        token_usage=per_activation,
                        steps=steps,
                    )
            except Exception as e:
                logger.warning(f"协作模式（非流式）子记录补记失败（已降级）: {e}")

            return {
                "message": result.get("response", ""),
                # 会话归属：非流式入口（multi_agent_service.run）落库用 request.conversation_id，
                # 不消费此字段；这里也回集群会话，避免上层误用子运行 ID
                "conversation_id": str(conversation_id) if conversation_id else result.get("conversation_id"),
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

    @staticmethod
    def _strip_conversation_id(event: str) -> str:
        """从子 Agent 事件中剥离 conversation_id 字段（S3：会话 ID 去污染）。

        子 Agent 运行（AgentRunService.run_stream sub_agent=True / handoffs）会
        在自己的 message / end 等事件里携带**子运行会话**的 conversation_id。
        上层一旦原样透传，前端 currentConversationId / syncConversationId 就会被
        子 Agent 会话顶掉 —— 下一轮请求打到别的 conversation，同一轮对话在
        库里分裂到另一条会话。
        会话 ID 的唯一权威来源是 orchestrator 的 start/end 事件（集群会话 ID）。
        其余字段（content/agent/usage/…）原样保留。
        """
        if not isinstance(event, str) or "data:" not in event:
            return event
        try:
            head, _, payload = event.partition("data:")
            data = json.loads(payload.strip())
            if not isinstance(data, dict) or "conversation_id" not in data:
                return event
            data.pop("conversation_id", None)
            return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception:
            return event

    def _set_merge_mode_actual(self, mode: str, reason: str = ""):
        """记录本轮结果整合实际执行的模式（S3：降级可观测）。

        execution_config.result_merge_mode 声明的是"想要"的模式；实际执行可能因
        单结果跳过合并、Master 模型/Key 缺失、整合异常等降级为拼接。该值写入
        end 事件的 merge_mode_actual 字段，前端/日志据此可见"配了 master 但实际
        是 smart 拼接"这类静默降级。reason 只进日志不进事件。
        """
        self._merge_mode_actual = mode
        if mode != "master" or reason:
            logger.info(
                "结果整合实际模式",
                extra={"merge_mode_actual": mode, "reason": reason},
            )

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
        execution_mode = self._execution_config.get("sub_agent_execution_mode", "parallel")

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
            task_results = await self._gather_limited(tasks)

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
        task_results = await self._gather_limited(tasks)

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
            task_results = await self._gather_limited(tasks)

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
        # 默认值必须是 master（与 schema 声明、流式路径 :1327 对齐）：
        # 旧配置缺 key 时若默认 smart，非流式会话永远走纯拼接，
        # 与流式行为不一致（同一应用两种调用方式给出不同质量的最终答案）。
        merge_mode = self._execution_config.get("result_merge_mode", "master")

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
        # 默认值必须是 master（与 schema 声明、流式路径 :1327 对齐）：
        # 旧配置缺 key 时若默认 smart，非流式会话永远走纯拼接，
        # 与流式行为不一致（同一应用两种调用方式给出不同质量的最终答案）。
        merge_mode = self._execution_config.get("result_merge_mode", "master")

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
            max_tokens = int(self._execution_config.get("merge_max_tokens", 8192))
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
            from app.core.models import RedBearChatModel

            # 获取 Master Agent 的模型配置
            default_model_config_id = self.config.default_model_config_id
            if not default_model_config_id:
                logger.warning("没有配置 Master Agent，使用简单整合")
                return self._smart_merge_results(results, strategy)

            # 获取模型视图（非解密视图，调用经模型服务 invoke 接缝）
            model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db,
                default_model_config_id,
                tenant_id=self.tenant_id,
            )

            logger.info(
                "使用 Master Agent 整合结果",
                extra={
                    "agent_count": len(agent_responses),
                    "strategy": strategy,
                    "has_original_question": bool(original_question)
                }
            )

            # 创建 LLM 实例
            _merge_params = self._resolve_merge_params()
            llm = RedBearChatModel.for_invoke(
                model_view,
                params={
                    "temperature": _merge_params["temperature"],
                    "max_tokens": _merge_params["max_tokens"],
                },
            )

            # 调用模型进行整合
            response = await llm.ainvoke(merge_prompt)

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
            self._turn_merge_tokens += int(merge_tokens or 0)

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
            from app.core.models import RedBearChatModel

            # 获取 Master Agent 的模型配置
            default_model_config_id = self.config.default_model_config_id
            if not default_model_config_id:
                logger.warning("没有配置 Master Agent，降级为 smart 拼接（分块流式）")
                async for _evt in self._smart_merge_results_stream(results, strategy):
                    yield _evt
                return

            # 获取模型视图（非解密视图，调用经模型服务 invoke 接缝）
            model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db,
                default_model_config_id,
                tenant_id=self.tenant_id,
            )

            logger.info(
                "开始 Master Agent 流式整合",
                extra={
                    "agent_count": len(agent_responses),
                    "strategy": strategy
                }
            )

            # 创建 LLM 实例（流式）
            # max_tokens 走 execution_config.merge_max_tokens（默认 8192）：
            # 写死 2000 会把长报告的整合结果截断，而整合输出正是用户看到的最终答案。
            _merge_params = self._resolve_merge_params()
            llm = RedBearChatModel.for_invoke(
                model_view,
                params={
                    "temperature": _merge_params["temperature"],
                    "max_tokens": _merge_params["max_tokens"],
                },
                streaming=True,
            )

            logger.info("开始流式调用 Master Agent LLM")

            # 流式调用模型进行整合
            try:
                chunk_count = 0
                logger.debug(f"开始流式调用，model={model_view.model_name}")

                async for chunk in llm.astream(merge_prompt):
                    chunk_count += 1

                    # S4：LangChain 流式把累计 usage 放在（通常最后一个）chunk 的
                    # usage_metadata —— 它是"累计值"不是"增量"，取末次值覆盖，
                    # 不累加（部分 provider 逐 chunk 回报累计值，累加会翻倍）。
                    _um = getattr(chunk, 'usage_metadata', None)
                    if isinstance(_um, dict) and _um.get("total_tokens"):
                        self._turn_merge_tokens = int(_um.get("total_tokens") or 0)

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
            return self._execution_config.get("force_merge_decomposition", True)

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

        # S10：流式并发限流。此前这里是裸 create_task 全量并发——`parallel_limit`
        # 只在非流式 `_gather_limited` 生效，而流式才是主路径（前端调试预览、正式
        # 聊天都走这里），等于护栏在用户看得见的那条路上是空的：N 个子 Agent 同时
        # 打模型，只剩各自的空闲超时兜底。
        #
        # 信号量必须**包住整个流的生命周期**（持有到该子 Agent 跑完），不能只包单次
        # yield——生成器 yield 出去就释放的话，并发数立刻回到全量，等于没限。
        sem = asyncio.Semaphore(self._parallel_limit())

        async def consume_stream(stream, stream_id):
            """消费单个流并放入队列（持信号量到流结束）"""
            nonlocal active_streams
            try:
                async with sem:
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
