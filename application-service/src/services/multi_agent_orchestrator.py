"""Multi-agent orchestrator - the Master Agent acts as the decision center"""
import uuid
import time
import json
import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional, AsyncIterator, Tuple, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..db import get_async_db_context
from ..models import MultiAgentConfig, AgentConfig, ModelConfig
from ..models.multi_agent_model import AggregationStrategy, OrchestrationMode
from .agent_registry import AgentRegistry
from .master_agent_router import MasterAgentRouter
from .conversation_state_manager import (
    ConversationStateManager,
    create_conversation_state_manager,
)
from ..errors import BusinessException, ResourceNotFoundException
from ..errors import BizCode
from ..utils.datetime_utils import utcnow_naive
from ..repositories.tool_repository import ToolRepository
from .model_service import ModelApiKeyService, ModelConfigService
from .multi_agent_variable_contract import (
    ClusterVariableBag,
    build_cluster_variable_bag,
    render_variables_in_text,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..schemas.model_schema import ModelInfo

# ──────────────────────────────────────────────────────────────────
# Supervisor loop (ReAct-style) — SubAgentTool bridge layer
# ──────────────────────────────────────────────────────────────────
# Supervisor = LangChainAgent (same engine as agent apps); tool list = published
# agent apps (agents-as-tools). Each sub-agent is wrapped as a BaseTool: calling
# the tool = dispatch, not calling it = final; termination = the model stops
# calling tools (native engine semantics).
# Sub-agent context comes only from the task brief the supervisor provides plus
# the variable bag — it never carries cluster conversation history
# (run_stream(sub_agent=True) with skip_save writes no messages, and
# _ensure_conversation only validates, never creates, so there is no cross-talk
# surface by design).

# Tool-name sanitization: LangChain tool names disallow Chinese characters and
# spaces (^[a-zA-Z0-9_-]+$); Chinese names/spaces map to underscores and the
# original name goes into description for the supervisor to choose by.


def _sanitize_tool_name(name: str, agent_id: str) -> str:
    """Sanitize a sub-agent tool name (Chinese chars/spaces -> underscores; empty name falls back to agent_id)."""
    cleaned = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "_-")) else "_"
        for ch in (name or "")
    ).strip("_")
    if not cleaned:
        cleaned = (agent_id or "agent").replace("-", "_")
    return f"delegate_{cleaned}"


# SubAgentConfig.role carries two meanings at once: routing reserved values
# (primary/secondary) and free text written by the frontend "description" input;
# the former must be excluded when building the brief.
_RESERVED_SUB_AGENT_ROLES = frozenset({"primary", "secondary"})


def _sub_agent_brief(agent_data: Dict[str, Any]) -> str:
    """Build a sub-agent brief (used by the supervisor roster and tool descriptions).

    Source priority:
    1. The "description" the user filled in for the cluster (stored in
       info["role"], excluding primary/secondary reserved values) + "capabilities"
       (info["capabilities"]);
    2. info["description"] (legacy-data compatibility);
    3. The sub-app's own description (config.description).
    Returns an empty string when all are empty; the caller decides the fallback text.
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
    """Factory/executor for sub-agent tools (the core of the bridge layer).

    Implemented as a class rather than a closure because the executor needs
    access to the orchestrator instance (ledger / execution tree / conversation
    stripping) and must push observability events back to the supervisor loop
    (agent_dispatch/agent_complete/agent_log). Events are pushed back through
    the event_sink async queue and uniformly formatted as SSE on the
    supervisor-loop side.

    Note: LangChainAgent wraps every tool with `_wrap_tools_with_call_limit` in
    __init__ (a language-level guard: the N+1th call is rejected and the model
    is told to give a final answer) — the factory must return a standard
    BaseTool that this wrapper recognizes.
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
        # event_sink is injected by the supervisor loop (asyncio.Queue holding SSE
        # strings); all observability events produced during tool execution go into
        # this queue and are sent downstream by the supervisor-loop side.
        self.event_sink: Optional[asyncio.Queue] = None
        # Tool display name (LangChain-legal character set)
        self.tool_name = _sanitize_tool_name(agent_name, agent_id)
        # Number of times the supervisor called this tool this turn (not a copy of
        # the engine counter — the engine's keeps incrementing even after a tool is
        # rejected; this only counts runs where the sub-agent actually executed,
        # used to determine loop_stop_reason: 0 calls = supervisor answered itself,
        # reaching the limit = guard triggered).
        self.calls = 0

    async def _emit_sse(self, event: str) -> None:
        """Send a sub-execution observability SSE event back to the supervisor loop (silently dropped when no sink is attached; never blocks execution)."""
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink.put_nowait(event)
        except Exception:
            pass

    async def _execute(self, task: str) -> str:
        """Tool executor: run a sub-agent (non-streamed result + streamed observability events pushed back).

        The string returned to the supervisor LLM = the sub-agent's final body
        text (full, no truncation or paraphrase — LangChain benchmark finding:
        distortion in the translation layer is the main error source for
        supervisors).
        Event surface: agent_dispatch (start) -> agent_log (trace snapshot,
        several times) -> agent_complete (wrap-up, with elapsed/token/output).
        All three events carry agent attribution fields; the frontend
        clusterStream.ts consumes them by existing key names, zero changes.
        """
        orch = self._orchestrator
        agent_data = orch.sub_agents.get(self.agent_id) if orch else None
        if not agent_data:
            return f"子 Agent 不可用: {self.agent_name}（{self.agent_id}）"

        # max_iterations = supervisor loop round limit (one round = the batch of
        # sub-agent calls issued by one supervisor decision; N concurrent calls in
        # the same round count as 1). Once the limit is reached, calls in the new
        # round are not executed; the limit is written into the tool return value
        # so the supervisor wraps up based on existing results — same mechanism as
        # the engine's _wrap_tools_with_call_limit, avoiding a hit on LangGraph
        # recursion_limit that the engine would swallow into a generic apology.
        #
        # Round detection: the next round's LLM decision must wait for the
        # previous batch of tools to fully return, so "no in-flight sub-agent call
        # at call start" means the first call of a new round; subsequent calls in
        # the same batch start while the former is still in flight. There is no
        # await between the check and the increment, so concurrent calls in a
        # batch do not interfere with one another.
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
        """Execute one sub-agent dispatch (the original executor after the round gate)."""
        self.calls += 1
        loop_started = time.time()

        # Sub-agent context = supervisor task brief + variable bag (cluster
        # conversation history is deliberately not carried). The variable bag was
        # already validated and defaulted at the entry point, so it is passed
        # through as-is; the sub-agent layer fills its own defaults
        # (prepare_variables_for_cluster) without re-deciding required fields.
        _bag = getattr(orch, "_cluster_variables", None)
        context = dict(_bag.values) if _bag is not None else {}
        sub_text = ""
        sub_error: Optional[str] = None

        # Go through the streaming executor (preserving the execution tree,
        # conversation stripping, and ledger accounting), accumulating the body
        # text as the return value; observability events are forwarded directly
        # from run_stream's native SSE (agent_dispatch/agent_complete carry
        # execution_id themselves — more accurate than hand-rebuilt bare event
        # bindings, and the wrap-up/trace fields on the draft_run side are
        # complete). start/end belong to the sub-run lifecycle
        # (_SUB_RUN_LIFECYCLE_EVENTS) and are filtered out, not passed through.
        #
        # Isolate the LangChain run context (critical): this executor runs as a
        # tool inside the supervisor engine's astream_events callback context. If
        # the sub-agent's LangChainAgent inherits parent callbacks via
        # var_child_runnable_config, its on_chat_model_stream would be yielded by
        # the supervisor's astream_events as supervisor body text — the loop layer
        # would then turn it into event: message sent to the frontend, mixing
        # sub-agent raw output into the main bubble and polluting the persisted
        # full_content text. Same isolation approach as workflow/base.py and
        # content_search.py: attach empty callbacks/tags/metadata to the child
        # run so its events only flow into its own stream.
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
                    # Strip the sub-conversation ID, then add agent attribution
                    # (same injection as the three-stage
                    # `_execute_supervisor_stream`): agent_dispatch/agent_complete
                    # natively carry execution_id/agent_id/agent_name (setdefault
                    # does not override existing values), but agent_log/
                    # agent_log_final only have {type, data} under data — without
                    # attribution, the frontend findAgentBlockIndex cannot match
                    # the block created by dispatch, the trace lands in a
                    # free-floating unknown block, the sub-agent block's agent_log
                    # stays empty -> the Runtime round-details entry never
                    # appears and the tool-call flow cannot be displayed.
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

        # Fallback wrap-up: when run_stream is interrupted by an exception,
        # agent_complete may not have been emitted and the frontend block would
        # stay in running — manually emit a bare wrap-up event.
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

    def build(self) -> "BaseTool":  # noqa: F821 - deferred type reference
        """Build a LangChain BaseTool (async executor + description).

        Sink attachment: the supervisor loop reaches SubAgentTool directly
        through the orchestrator._loop_tool_instances instance list (populated
        by _build_supervisor_agent), not by reverse lookup from LangChain tool
        objects — the engine's _wrap_tools_with_call_limit wraps _run/_arun in
        place, so recovering the closure from the tool object is unreliable.
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
            """Dispatch the subtask to this sub-agent and return its full output."""
            return await instance._execute(task or "")

        # The Chinese name/role description goes into description (tool names
        # are restricted to ^[a-zA-Z0-9_-]+, so the supervisor can only identify
        # agents by description). Docstring fallback + explicit assignment for
        # double safety: some langchain versions refuse to build a tool when
        # description is empty.
        delegate_sub_agent.description = (
            f"子 Agent「{self.agent_name}」。{self.description}。"
            f"调用它执行你无法直接完成的子任务，返回该 Agent 的完整输出。"
        )
        return delegate_sub_agent


# Whitelist of cluster-run "observability events": these events carry no
# content and would be discarded as "non-content events" by the sub-agent event
# parsing loop, leaving the running panel without trace / dispatch wrap-up.
# Only the four events that actually drive the UI: trace snapshots
# (agent_log/agent_log_final) + block open/close
# (agent_dispatch/agent_complete). tool_start/tool_end are not forwarded —
# agent_log's trace already contains tool_calls, and forwarding them again only
# adds SSE traffic.
# All other events (especially sub_usage) stay filtered as before so the
# existing token accounting is unchanged.
_CLUSTER_OBSERVABILITY_EVENTS = frozenset({
    "agent_log",
    "agent_log_final",
    "agent_dispatch",
    "agent_complete",
})

# The sub-run (sub-agent / handoffs) own start / end events are "sub-run
# lifecycle" events, not cluster conversation events. Passing them through
# as-is would hit two traps at once:
#  1) one cluster conversation would show two starts and two ends, so upper
#     layers (frontend / external App API) could not delimit a turn by
#     start...end;
#  2) the message_id / user_message_id in a sub-run start are temporary ids
#     generated by run_stream's internal uuid4 (sub-runs use skip_save and are
#     not persisted), and the frontend applyModelMessageId would overwrite the
#     real id of the cluster bubble with them, making message_id-based actions
#     (copy / regenerate / feedback) point to a nonexistent message.
# Sub-run visibility is provided by agent_log / agent_dispatch / agent_complete,
# consistent with the whitelist path.
_SUB_RUN_LIFECYCLE_EVENTS = frozenset({"start", "end"})


def _trace_total_tokens(agent_log_item: Any) -> int:
    """Extract the per-round accumulated token total from a LangChainAgent agent_log event.

    The int finally yielded by the engine's `chat_stream` only counts the **last**
    LLM call (`AgentTraceRecorder.finalize` overwrites meta.total_tokens with the
    final value), so reading the final value directly in a multi-round supervisor
    loop would lose tokens. iterations[].llm.tokens is accumulated per round by
    `finish_llm` (langchain_agent.py:149) — use it to align with the ledger.
    Returns 0 when unavailable.
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
    """Extract the event name from an SSE string (returns None if parsing fails)."""
    if not isinstance(event, str) or not event.startswith("event:"):
        return None
    try:
        return event.split("event:", 1)[1].split("\n", 1)[0].strip() or None
    except Exception:
        return None


class MultiAgentOrchestrator:
    """Multi-agent orchestrator - coordinates multiple agents to complete tasks together"""

    def __init__(self, db: Session | AsyncSession, config: MultiAgentConfig):
        """Initialize the orchestrator.

        Args:
            db: Database session
            config: Multi-agent configuration
        """
        self.db = db
        self.config = config
        self.registry = AgentRegistry(db)

        # Compatibility handling: legacy orchestration_mode values map to new ones
        # collaboration | supervisor are the new values; other legacy values default to supervisor
        self._normalized_mode = self._normalize_orchestration_mode(config.orchestration_mode)

        # Load the master Agent
        # self.master_agent = self._load_agent(config.master_agent_id)
        # self. config.d
        self.default_model_config_id = config.default_model_config_id
        self.model_parameters = config.model_parameters
        # execution_config snapshot: during streaming execution the ORM session
        # may commit and expire config attributes; reading
        # self.config.execution_config afterwards would trigger a synchronous
        # lazy load and raise MissingGreenlet (not allowed on async sessions).
        # At construction time the attributes are still loaded, so copy a plain
        # dict here for runtime reads.
        _exec_cfg = getattr(config, "execution_config", None)
        self._execution_config: Dict[str, Any] = dict(_exec_cfg) if isinstance(_exec_cfg, dict) else {}
        # Likewise snapshot the scalar/JSON fields read again at runtime: during
        # the streaming wrap-up phase (cluster_memory_enabled, collaboration
        # activation records, etc.) config is already expired and reading ORM
        # attributes directly would raise MissingGreenlet the same way.
        _sup_cfg = getattr(config, "supervisor_config", None)
        self._supervisor_config_snapshot: Dict[str, Any] = _sup_cfg if isinstance(_sup_cfg, dict) else {}
        self._app_id = getattr(config, "app_id", None)
        self._master_agent_id = getattr(config, "master_agent_id", None)
        self._master_agent_name = getattr(config, "master_agent_name", None)
        self.tenant_id = None
        self.sub_agents = {}
        # Sub-agent entries after create() resolution (agent_id replaced with a
        # valid release ID); None when constructed without create(), and
        # downstream falls back to the original config.
        self._effective_sub_agent_entries: Optional[List[Dict[str, Any]]] = None
        # Routing state backend. Only the in-memory fallback is set here; the
        # real Redis backend is injected in create() once tenant_id is resolved
        # (the key prefix needs tenant isolation).
        self.state_manager = ConversationStateManager()
        self.master_model_config = None
        self.router = None

        # ── Cluster execution attribution (multi-agent logging) ─────────
        # The master (orchestrating) Agent's execution id: used as
        # parent_execution_id when sub-agents are persisted. Carried as an
        # instance attribute instead of a per-call-site argument —
        # _execute_sub_agent_stream has 4 call sites and _execute_sub_agent has
        # 10; adding a parameter to each would inevitably miss one (a missed
        # pass on the streaming path previously broke the foreign key).
        self.current_execution_id: Optional[uuid.UUID] = None
        self.current_conversation_id: Optional[uuid.UUID] = None
        self._master_started_at: Optional[float] = None
        # The mode actually executed for this turn's merge (master/smart/skip);
        # emitted in the end event when execute_stream finishes
        # (merge_mode_actual field). None = the mode never reached the merge
        # stage (collaboration mode, etc.).
        self._merge_mode_actual: Optional[str] = None
        # Per-turn token ledger (UsageLedger v1).
        # Three layers: routing (Master routing decision) / sub (sub-agent
        # execution) / merge (result merging). Accounting is centralized in one
        # wrapper layer (_execute_sub_agent_stream/_execute_sub_agent) so
        # individual branches do not each parse sub_usage strings — outer entry
        # points and the frontend always read the total from the end event's
        # usage field; assembling SSE strings for counting is forbidden.
        # prompt/completion stay 0 for now because emitters only carry a total.
        self._turn_routing_tokens: int = 0
        self._turn_sub_tokens: int = 0
        self._turn_merge_tokens: int = 0
        # The cluster variable bag in effect for this turn (produced by
        # entry-point validation/rendering), shared by both modes: supervisor
        # puts it into task_analysis["initial_context"], collaboration into the
        # handoffs node's system_prompt / message rendering. Observations
        # (missing required fields, undefined variables) also hang off it.
        self._cluster_variables: Optional[ClusterVariableBag] = None
        # Runtime state of the supervisor loop
        # (orchestration_mode=supervisor_loop).
        # _loop_tool_instances: this turn's SubAgentTool executor list (tool
        # event sink attachment + call counting); _loop_stop_reason: this turn's
        # stop reason (self-answer / wrap-up after dispatch / guard triggered),
        # emitted in the end event for guard observability.
        # _supervisor_loop_fallback was removed — the loop no longer falls back
        # to the three-stage path; failures raise directly.
        self._loop_tool_instances: list = []
        self._loop_stop_reason: Optional[str] = None
        # max_iterations = supervisor loop round limit. _loop_rounds: supervisor
        # rounds already started this turn; _loop_inflight: current in-flight
        # sub-agent calls (when 0, the next call starts a new round);
        # _loop_iteration_limit_hit: the round gate rejected a call (written by
        # SubAgentTool), used to record loop_stop_reason accurately as
        # max_iterations (the soft limit does not raise GraphRecursionError).
        self._loop_rounds: int = 0
        self._loop_inflight: int = 0
        self._loop_iteration_limit_hit: bool = False
        # Chat features: aligned with Agent apps. Existing clusters have no
        # features -> empty dict = all off, behavior unchanged.
        from .cluster_chat_features import get_features as _get_cluster_features
        self._features: Dict[str, Any] = _get_cluster_features(self._supervisor_config_snapshot)
        # Supervisor web search = features.web_search.enabled and web_search
        # requested (sub-agent web search still goes through _loop_web_search)
        self._loop_supervisor_web_search: bool = False
        # Deep thinking = model_parameters.deep_thinking and thinking requested
        self._loop_deep_thinking: bool = False
        # Opening statement for a new conversation (passed in after entry
        # parsing; appended to the end of the supervisor history; None = not injected)
        self._loop_opening_statement: Optional[str] = None
        # Execution mode: the cluster supervisor only implements in_process for
        # now; sandbox requests fall back and are explicitly marked in the end event
        self._execution_mode_requested: Optional[str] = None
        self._execution_mode_actual: str = "in_process"
        # This turn's runtime state (suggested questions / citations / TTS / emotion);
        # reset each turn in _prepare_turn_chat_features
        self._loop_citations: list = []
        self._turn_extras: Dict[str, Any] = {}
        self._turn_emotion_detection: Any = None
        self._turn_user_message_id: Optional[uuid.UUID] = None
        # context_engine: only populated when features.context_engine.enabled and the plugin is available
        self._turn_ctx_history: Optional[List[Dict[str, Any]]] = None
        self._turn_used_context_engine: bool = False
        self._turn_ctx_provider: Optional[str] = None
        self._turn_ctx_model_config_id: Any = None
        # Regeneration scenario: truncate the supervisor history to before this time
        # (= the created_at of the original user message) so the re-run context matches
        # the round being regenerated. None = no truncation (normal new turn).
        self._regenerate_before: Any = None

    def _prepare_turn_chat_features(
        self,
        web_search: bool,
        thinking: bool,
        opening_statement: Optional[str],
        execution_mode: Optional[str],
        user_message_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Resolve this turn's "chat features" runtime state (aligned with Agent apps).

        Backward compatible: when existing clusters have no features and requests
        carry no thinking/opening/execution_mode, everything resolves to
        off/None and supervisor behavior is identical to before.
        """
        from .cluster_chat_features import (
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
        # Reset every turn so reusing the same orchestrator across turns does not leak state
        self._loop_citations = []
        self._turn_extras = {}
        self._turn_emotion_detection = None
        self._turn_user_message_id = user_message_id
        # Reset context_engine runtime state each turn
        self._turn_ctx_history = None
        self._turn_used_context_engine = False
        self._turn_ctx_provider = None
        self._turn_ctx_model_config_id = None

    def context_engine_after_turn_args(self, conversation_id: Any) -> Optional[Dict[str, Any]]:
        """If context_engine ran this turn, return the BatchPersistQueue "after_turn" task args; otherwise None (always None for existing clusters)."""
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
        model_view: Any,
    ) -> str:
        """When features.context_engine is on, use the context engine to build the supervisor system_prompt (with summary) and history.

        Not enabled / plugin missing / any exception -> return system_prompt
        as-is, leave history empty, and the caller falls back to
        _load_cluster_history (same as before).
        """
        ctx_cfg = (self._features or {}).get("context_engine")
        if not (isinstance(ctx_cfg, dict) and ctx_cfg.get("enabled")) or not conversation_id or self.db is None:
            return system_prompt
        try:
            from ..config import current_settings
            from .context_engine_manager import ContextEngineManager

            provider = getattr(model_view, "provider", None)
            model_config_id = getattr(model_view, "model_config_id", None) or self.default_model_config_id
            prepared = await ContextEngineManager(self.db).prepare_app_agent_input(
                features=self._features,
                conversation_id=conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id)),
                system_prompt=system_prompt,
                current_input=getattr(self, "_loop_entry_message", "") or "",
                current_provider=provider,
                legacy_max_history=current_settings().agent_max_history,
                scope_key="cluster",
                model_config_id=model_config_id,
            )
            if not prepared:
                return system_prompt
            new_prompt, history = prepared
            # Same convention as _load_cluster_history: the supervisor chain only consumes plain-text turns
            self._turn_ctx_history = [
                {"role": h["role"], "content": h["content"]}
                for h in (history or [])
                if isinstance(h.get("content"), str)
            ]
            self._turn_used_context_engine = True
            self._turn_ctx_provider = provider
            self._turn_ctx_model_config_id = model_config_id
            return new_prompt or system_prompt
        except Exception as e:  # noqa: BLE001 - context engine is an additive capability; degrade to legacy history
            logger.warning(f"集群 context_engine 准备失败（已降级为旧历史）: {e}")
            self._turn_ctx_history = None
            self._turn_used_context_engine = False
            return system_prompt

    async def _resolve_loop_history(self, conversation_id: Optional[uuid.UUID]) -> List[Dict[str, Any]]:
        """Supervisor history: use context_engine results when active this turn, otherwise the cluster's 20-turn history; then append the opening statement."""
        if self._turn_used_context_engine and self._turn_ctx_history is not None:
            return self._with_opening(list(self._turn_ctx_history))
        return self._with_opening(await self._load_cluster_history(conversation_id))

    def _turn_end_extras(self) -> Dict[str, Any]:
        """Chat-feature extras for the end event; empty dict when no new capability is active (legacy frontend/contract unchanged)."""
        extras: Dict[str, Any] = {}
        if self._execution_mode_requested:
            extras["execution_mode_actual"] = self._execution_mode_actual
        # suggested_questions / citations / audio_url / audio_status: keys exist only when the corresponding feature is on
        extras.update(self._turn_extras or {})
        return extras

    def _supervisor_api_key_dict(self, api_key_config: Any) -> Dict[str, Any]:
        """Supervisor channel-resolution shell -> TTS dict (only local TTS synthesis consumes plaintext; all other consumers use the non-decrypted view)."""

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
        """Start the background emotion-detection task only when features.emotion_reply is on; returns None with zero overhead when off."""
        try:
            from ..memory.emotion.emotion_resolver import start_detection

            self._turn_emotion_detection = start_detection(self._features, message)
        except Exception as e:  # noqa: BLE001 - emotion sensing is an additive capability; failure must not block the conversation
            logger.warning(f"集群情绪识别启动失败（已跳过）: {e}")
            self._turn_emotion_detection = None

    async def _setup_turn_tts(self):
        """Create streaming TTS only when features.text_to_speech is on; returns (text_queue, audio_url, tts_task)."""
        tts_cfg = (self._features or {}).get("text_to_speech")
        if not (isinstance(tts_cfg, dict) and tts_cfg.get("enabled")):
            return None, None, None
        try:
            from .draft_run_service import AgentRunService

            text_queue: asyncio.Queue = asyncio.Queue()
            audio_url, tts_task = await AgentRunService(self.db)._generate_tts_streaming(
                self._features,
                await self._resolve_supervisor_tts_dict(),
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
        model_view: "ModelInfo",
        audio_url: Optional[str] = None,
        tts_task: Optional["asyncio.Task"] = None,
        non_stream_tts: bool = False,
    ) -> None:
        """Produce end-event extras after the supervisor body finishes: suggested questions / citations / audio.

        Each item is written to self._turn_extras only when the corresponding
        feature is on (off by default = no new keys, contract unchanged).
        Any single item failing only logs a warning; the body and end event are
        unaffected.
        """
        features = self._features or {}
        extras: Dict[str, Any] = {}
        try:
            from .draft_run_service import AgentRunService

            agent_service = AgentRunService(self.db)

            sq_cfg = features.get("suggested_questions_after_answer")
            if isinstance(sq_cfg, dict) and sq_cfg.get("enabled"):
                extras["suggested_questions"] = await agent_service._generate_suggested_questions(
                    features, final_content, model_view, {}
                )

            cit_cfg = features.get("citation")
            if isinstance(cit_cfg, dict) and cit_cfg.get("enabled"):
                # Only citations from the supervisor's own knowledge base; sub-agent internal retrievals are not aggregated
                extras["citations"] = agent_service._filter_citations(
                    features, list(self._loop_citations or [])
                )

            tts_cfg = features.get("text_to_speech")
            if non_stream_tts and isinstance(tts_cfg, dict) and tts_cfg.get("enabled"):
                audio_url = await agent_service._generate_tts(
                    features,
                    final_content,
                    await self._resolve_supervisor_tts_dict(),
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
        """Append the new-conversation opening statement to the end of the history (same as Agent apps); returns the history unchanged when there is none."""
        if not self._loop_opening_statement:
            return history
        return list(history or []) + [{"role": "assistant", "content": self._loop_opening_statement}]

    async def _prepare_loop_files(
        self,
        files: Optional[List[Any]],
        model_view: "ModelInfo",
        message: str,
        supervisor: Any,
    ) -> tuple:
        """Process the supervisor's attachments for this turn (aligned with Agent apps: validate -> MultimodalService -> image manifest).

        Files go only to the supervisor (sub-agents never receive them).
        Backward compatible: without files, return (message, None) directly and
        trigger no new logic; with files but features.file_upload off for the
        cluster, fail explicitly, matching the Agent app wording
        "该应用未开启文件上传功能".

        Returns:
            (llm_message, processed_files): llm_message is the user message with
            the image manifest appended; processed_files is the multimodal
            content usable by the LLM (for chat/chat_stream's files parameter).
        """
        # For this turn's persistence (entry layer reads history_files / provider)
        self._turn_processed_files = None
        self._turn_files_provider = None
        if not files:
            return message, None

        fu = self._features.get("file_upload") if isinstance(self._features, dict) else None
        if not (isinstance(fu, dict) and fu.get("enabled")):
            raise BusinessException("该集群未开启文件上传功能", BizCode.BAD_REQUEST)

        from .draft_run_service import AgentRunService, build_uploaded_images_manifest
        from .multimodal_service import MultimodalService

        AgentRunService._validate_file_upload(self._features, files)

        provider = getattr(model_view, "provider", None) or "openai"
        multimodal_service = MultimodalService(self.db, model_view)
        processed_files = await multimodal_service.process_files(
            files,
            document_image_recognition=bool(fu.get("document_image_recognition", False)),
            workspace_id=await self._cluster_workspace_id(),
            file_upload_config=fu,
        )
        logger.info(f"集群主管处理了 {len(processed_files)} 个文件")

        # Reinject this turn's image allowlist into the supervisor's knowledge-base tools (search-by-image)
        for tool in getattr(supervisor, "tools", None) or []:
            set_uploaded_files = getattr(tool, "set_uploaded_files", None)
            if callable(set_uploaded_files):
                set_uploaded_files(files)

        # List this turn's images and their indices in the user message so the model can explicitly trigger image retrieval on demand (without polluting the persisted original text)
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
        """Entry variable contract: definition collection -> required validation -> defaults -> observability trail.

        Recomputed every turn (the same orchestrator instance runs multiple
        turns and sub-agent configs may have been updated).
        """
        # Regeneration (when _regenerate_before is set): variables are restored from
        # the values persisted on the first turn; stored conversations without saved
        # variables must not be blocked by missing required ones (the first turn already
        # passed entry validation), so relax to non-raising observation only.
        strict_required = False if self._regenerate_before is not None else None
        bag = build_cluster_variable_bag(
            self.config, self.sub_agents, variables, strict_required=strict_required
        )
        self._cluster_variables = bag
        return bag

    def _render_entry_message(self, message: str, values: Optional[Dict[str, Any]]) -> str:
        """Entry message rendering: replace only {{name}} placeholders that actually exist in the variable bag.

        The rendered text is what gets dispatched to sub-agents / handoffs —
        previously {{variables}} in user messages were never rendered, so
        configured variables had no effect. Braces typed by the user that are
        not in the variable bag are kept as-is.
        """
        return render_variables_in_text(message or "", values or {})

    @staticmethod
    async def _maybe_await(value):
        if inspect.isawaitable(value):
            return await value
        return value

    async def _db_get(self, model, identity, db=None):
        session = self.db if db is None else db
        if session is None:
            return None
        return await self._maybe_await(session.get(model, identity))

    @asynccontextmanager
    async def _sub_agent_db(self):
        """DB session owned by a single sub-agent execution.

        ``self.db`` is the orchestrator's request-scoped session. Sub-agents can
        run concurrently (parallel delegation / collaboration / supervisor tool
        batches) and an AsyncSession must not be used concurrently, so each
        execution opens its own session. Sync sessions (tests, sync hosts) and
        None fall back to ``self.db`` unchanged.
        """
        if isinstance(self.db, AsyncSession):
            async with get_async_db_context() as db:
                yield db
        else:
            yield self.db

    # ──────────────────────────────────────────────────────────────────
    # Multi-agent logging: lifecycle of the master execution record
    # ──────────────────────────────────────────────────────────────────

    async def _ensure_master_execution(
        self,
        conversation_id: Optional[uuid.UUID],
        message_id: Optional[uuid.UUID] = None,
    ) -> Optional[uuid.UUID]:
        """Create an agent_executions record for the master (orchestrating) Agent.

        Multi-agent conversations previously had **no** execution record at all
        (app_chat_service calls the orchestrator directly and only enqueues
        messages for persistence), so the node in the log details had nowhere to
        attach and sub-agents had no parent to find. This method fills that gap;
        when sub-agents are persisted, this record's id is used as their
        parent_execution_id.

        Why no message_id at creation time: this turn's assistant message is
        persisted asynchronously by BatchPersistQueue (later than this method
        runs), so the messages row does not exist yet; writing it here would
        trigger an FK violation and degrade the whole observability chain.
        message_id is instead backfilled by app_chat_service after the message
        is persisted (after save_messages inside the batch) — see PersistTask
        "link_agent_execution_message".

        Observability failures never block the conversation: any exception only
        logs a warning and current_execution_id stays None (sub-agents are then
        persisted with parent_execution_id=None, and the details side falls back
        to "same conversation + nearest in time").
        """
        self.current_conversation_id = conversation_id
        self._master_started_at = time.time()
        if self.db is None or not self._app_id or not conversation_id:
            return None
        try:
            from .draft_run_service import AgentRunService

            conv_uuid = conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
            svc = AgentRunService(self.db)
            self.current_execution_id = await svc._create_agent_execution_async(
                app_id=self._app_id,
                conversation_id=conv_uuid,
                # The master Agent has no agent_configs.id: config.master_agent_id is itself a release id
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
            # Observability chain failures must not affect the conversation
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
        """Finalize the master execution record; failures likewise only log a warning.

        The variable-contract observations for this turn (definition sources,
        missing required fields, defaulted values, undefined variables) are also
        written into meta_data — previously the root cause of "configured
        variables had no effect" in cluster scenarios had no place to land at all.
        """
        execution_id = self.current_execution_id
        if execution_id is None or self.db is None:
            return
        try:
            from .draft_run_service import AgentRunService

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
        """Parse an SSE string into (event name, data). Returns (None, {}) on parse failure; the caller treats it as pass-through."""
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
        """Collaboration mode: open a sub-agent execution record per node activation.

        The collaboration main chain is handoffs_service (LangGraph handoff) and
        does **not** go through AgentRunService.run_stream, so no sub-execution
        record is produced automatically and one must be created manually here.
        The same Agent activated multiple times yields multiple rows with
        distinct execution_ids.
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
            from .draft_run_service import AgentRunService

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
                # Same convention as supervisor mode: store the task received at
                # activation into meta_data, so the details page's "input" does
                # not depend on the shape of steps (steps also carry a head, but
                # meta.task is more stable)
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
        """Collaboration mode: finalize a sub-agent activation record.

        steps are assembled by the caller at activation granularity (the Agent's
        input/output + the handoffs it initiated); collaboration mode has no
        AgentTraceRecorder trace, so steps are its "call-chain detail".
        """
        if execution_id is None or self.db is None:
            return
        try:
            from .draft_run_service import AgentRunService

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
        """Collaboration mode (non-streaming): backfill one sub-agent activation record in one shot.

        The non-streaming path has no per-activation real-time events
        (agent/handoff/end), so after the run finishes it does "open then close"
        for each entry in handoff_history. Shares _open/_close with the streaming
        path so both modes produce the same agent_executions tree shape
        (master + N x sub). Observability failures likewise only degrade, never
        block.
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
        """Attribution fields for in-flight sub-agent events.

        No execution_id: the sub-execution record is created inside run_stream
        and the orchestration layer does not know the id at this point; the
        frontend first binds blocks by agent_id/agent_name, then backfills with
        the execution_id carried in agent_dispatch / agent_complete (when the
        same Agent is activated multiple times in collaboration mode,
        execution_id is the unique key).
        """
        return {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "parent_execution_id": str(self.current_execution_id) if self.current_execution_id else None,
            "orchestration_mode": self._normalized_mode,
        }

    def _inject_agent_meta(self, event: str, meta: Dict[str, Any]) -> str:
        """Inject agent attribution fields into the SSE event's data; event name and type stay unchanged.

        Only the `data:` line is modified (json.loads -> setdefault ->
        json.dumps); the `event:` line and the trailing blank line are preserved.
        setdefault guarantees same-named fields already present on the event are
        not overridden (e.g. agent_name from handoffs).
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
        """Sub-agent body-text event (**aggregated delivery**: the whole body is sent once, with no per-token chunks).

        The body only accumulates while the sub-agent runs and is sent once when
        that Agent finishes. Rationale:
        1) The event has no incremental semantics — consumers can do nothing
           with chunks except concatenate them, yet one cluster conversation
           would gain hundreds to thousands of extra events;
        2) The running panel's "live feel" comes from `agent_log` trace
           snapshots (a full snapshot is pushed after each round's llm/tool),
           unrelated to `sub_agent_message`;
        3) The frontend `streamHandlers.ts` does not consume this event anyway,
           so the change has no UI impact.

        A new event is still emitted each time (content differs when the same
        Agent is called repeatedly), at "one event per execution" granularity.
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
            from ..models import App
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

        # Build the state manager only after the tenant is known — the key
        # prefix `conv_state:{tenant}:{conv}` needs tenant_id; the factory
        # degrades to in-memory when Redis is unavailable.
        orchestrator.state_manager = create_conversation_state_manager(
            tenant_id=str(orchestrator.tenant_id) if orchestrator.tenant_id else None
        )

        from .multi_agent_release_resolver import aresolve_effective_release_id

        effective_entries: List[Dict[str, Any]] = []
        for sub_agent_info in config.sub_agents:
            # Version policy: in the DB, agent_id is the sub-agent's app ID
            # (legacy data holds a release ID; the resolver layer discriminates
            # by value). Here the effective release is resolved per
            # release_policy: current = the app's released version at this
            # moment, pinned = release_id. info is copied, never mutating the
            # config object; the effective release ID is written back to
            # info["agent_id"] and used as the key everywhere at runtime
            # (sub_agents keys, routing, execution records). Non-strict: a
            # failed current resolution for legacy shapes falls back to the
            # anchor version so one takedown does not drag down the whole
            # cluster.
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
        # Downstream (task analysis / collaboration handoffs) map release IDs to sub_agents, so the resolved entries must be used
        orchestrator._effective_sub_agent_entries = effective_entries

        # Both "supervisor-style" modes need a master model — supervisor uses it
        # for routing/merging, supervisor_loop to drive the supervisor ReAct
        # engine (_resolve_supervisor_model_view).
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

        # MasterAgentRouter only serves the three-stage path: supervisor_loop
        # delegates dispatch entirely to the supervisor LLM and burns no routing
        # calls (the loop became clearer once it was lifted from a
        # supervisor-internal branch to a standalone mode).
        if orchestrator._normalized_mode == OrchestrationMode.SUPERVISOR:
            orchestrator.router = MasterAgentRouter(
                db=db,
                master_model_config=orchestrator.master_model_config,
                model_parameters=orchestrator.model_parameters,
                sub_agents=orchestrator.sub_agents,
                state_manager=orchestrator.state_manager,
                tenant_id=orchestrator.tenant_id,
                # The keyword-rule fast path was removed; enable_rule_fast_path
                # is no longer passed (MasterAgentRouter dropped that parameter)
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
        """Normalize orchestration_mode, accepting legacy values.

        Args:
            mode: The raw orchestration_mode value

        Returns:
            The normalized mode: collaboration / supervisor / supervisor_loop
        """
        # supervisor_loop must be checked first — it is a prefix-superset of
        # supervisor, and reversed order would let supervisor swallow it (a
        # regression test locks in this invariant).
        if mode in [OrchestrationMode.SUPERVISOR_LOOP, "supervisor_loop"]:
            return OrchestrationMode.SUPERVISOR_LOOP
        if mode in [OrchestrationMode.SUPERVISOR, "supervisor"]:
            return OrchestrationMode.SUPERVISOR
        # All other values (including legacy sequential, parallel, conditional, loop and collaboration) map to collaboration
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
        regenerate_before: Any = None,
    ):
        """Execute a multi-agent task (streaming response).

        Args:
            message: User message
            conversation_id: Conversation ID
            user_id: User ID
            variables: Variable parameters
            use_llm_routing: Whether to use LLM routing
            web_search: Whether to enable web search
            memory: Whether to enable memory
            storage_type: Storage type
            user_rag_memory_id: User RAG memory ID
            message_id: This turn's assistant message ID (written into the master execution record so log details can attach nodes by message)
            regenerate_before: History truncation time for the regeneration scenario
                (= the created_at of the original user message); when set, the
                supervisor history only sees messages before it; None = normal new
                turn (legacy behavior)

        Yields:
            A stream of SSE-formatted events
        """

        start_time = time.time()
        # Regeneration: history truncation hook (consumed only by the supervisor
        # chain's _load_cluster_history; default None = legacy behavior)
        self._regenerate_before = regenerate_before

        # Reset the merge-mode observation value at the start of each turn (the same orchestrator instance may run multiple turns)
        self._merge_mode_actual = None
        # Reset the token ledger each turn (reusing the same orchestrator instance across turns must not leak accounting)
        self._turn_routing_tokens = 0
        self._turn_sub_tokens = 0
        self._turn_merge_tokens = 0
        # Reset supervisor-loop runtime state each turn (guard decisions never span turns)
        self._loop_stop_reason = None
        # Chat-feature runtime state (web search / deep thinking / opening statement / execution mode); all defaults = legacy behavior
        self._prepare_turn_chat_features(
            web_search, thinking, opening_statement, execution_mode, user_message_id
        )

        # Entry variable contract — one-shot validation + defaults + rendering
        # this turn's message. Both modes share the same variable bag, so
        # "configured variables should take effect" feels consistent with
        # single-Agent apps.
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

        # Master execution record: sub-agent records need it as parent, and log details need it to attach nodes
        await self._ensure_master_execution(conversation_id, message_id)

        try:
            # Send the start event.
            # conversation_id/message_id are mandatory: the outer entry point and
            # the orchestrator each send a start in the multi-agent stream. If
            # this event lacks conversation_id, the experience-share frontend
            # (streamHandlers start branch) overwrites the captured conversation
            # ID and setConversationId never runs at turn end -> the next turn
            # carries conversation_id=null -> the backend creates a new
            # conversation every turn ("one conversation per input").
            yield self._format_sse_event("start", {
                "mode": self._normalized_mode,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "message_id": str(message_id) if message_id else None,
                "timestamp": time.time()
            })

            # 2. Execute according to mode (streaming)
            # Collaboration mode: agents can hand off to each other (via handoffs_service)
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
            # Supervisor mode (three-stage):
            # routing decision (MasterAgentRouter) -> collective execution -> final merge.
            # result_merge_mode / aggregation_strategy / merge_max_tokens all apply in full.
            # The loop previously toggled in from here and defaulted to the ReAct
            # loop; it has since been lifted to a standalone mode (see the
            # SUPERVISOR_LOOP branch below), so this branch has no more
            # splitting — supervisor semantics are predictable for users.
            elif self._normalized_mode == OrchestrationMode.SUPERVISOR:
                # variables go into initial_context and are then passed down as
                # the sub-agents' variables (AgentRunService uses them to render
                # each sub-agent's system_prompt). Pass a copy: sequential/
                # parallel collaboration appends result_from_* into
                # initial_context, which must not pollute this turn's variable
                # bag in reverse (especially dangerous when one instance runs
                # multiple turns).
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
            # Supervisor Loop mode: supervisor = ReAct engine, sub-agents = tools.
            # Dispatch is entirely up to the supervisor LLM — it can answer
            # itself or assign more work across rounds; no routing, no merging
            # (final is the final answer). **No fallback to the three-stage
            # path** — master model unavailable / no sub-agents / no body text
            # all raise BusinessException inside the loop, and the except at the
            # end of this method handles wrap-up uniformly
            # (_finalize_master_execution(failed) + error event), keeping errors
            # attributable.
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

            # Send the end event.
            # conversation_id/message_id are mandatory: aligned with single-Agent
            # apps (whose end event carries both; the frontend backfills
            # conversation and message IDs from them). When missing in
            # multi-agent flows, one missed delivery on the "captured only from
            # start" path makes the next turn carry a null conversation ID and
            # the backend creates a new conversation every turn
            # (experience-share conversations split in the DB).
            # merge_mode_actual: the mode actually executed for this turn's
            # merge. None means the turn never reached the merge stage
            # (collaboration, etc.); "master" = supervisor LLM summarizing;
            # "smart" = concatenation (configured concatenation, or degradation
            # on missing model/key, merge exception, etc. — the frontend can
            # show a "concatenation mode" marker instead of the previous silent
            # degradation).
            # usage: the single authoritative accounting for this turn's tokens
            # (routing/sub/merge layers + total). Outer entry points
            # (app_chat_service / multi_agent_service) no longer intercept
            # sub_usage strings to accumulate on their own; everything reads
            # from this event.
            _usage_total = self._turn_routing_tokens + self._turn_sub_tokens + self._turn_merge_tokens

            # Finalize the master execution record before publishing end: the
            # client disconnects as soon as it sees end, which cancels this
            # generator and would otherwise skip the finalize step.
            await self._finalize_master_execution(
                status="completed",
                elapsed_time=time.time() - start_time,
                token_usage={
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": _usage_total,
                },
            )

            yield self._format_sse_event("end", {
                "elapsed_time": elapsed_time,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "message_id": str(message_id) if message_id else None,
                "merge_mode_actual": self._merge_mode_actual,
                # Supervisor loop stop reason (only set in supervisor_loop mode):
                # direct_answer = supervisor answered itself (zero dispatch);
                # final_after_dispatch = wrap-up after dispatch;
                # tool_call_limit/max_iterations = guard triggered; error = exception.
                "loop_stop_reason": self._loop_stop_reason,
                # Chat-feature extras (execution_mode_actual, etc.); empty when no new capability is active, contract unchanged
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
            # Send the error event
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
        regenerate_before: Any = None,
    ) -> Dict[str, Any]:
        """Execute a multi-agent task (based on Master Agent decisions).

        Args:
            message: User message
            conversation_id: Conversation ID
            user_id: User ID
            variables: Variable parameters
            use_llm_routing: Whether to use LLM routing (kept for compatibility; the Master Agent is always used in practice)
            message_id: This turn's assistant message ID (written into the master execution record)
            storage_type: Storage type (passed through to the collaboration call context)
            user_rag_memory_id: User RAG memory ID (same as above)
            regenerate_before: History truncation time for the regeneration scenario
                (= the created_at of the original user message); when set, the
                supervisor history only sees messages before it; None = normal new
                turn (legacy behavior)

        Returns:
            Execution result
        """
        start_time = time.time()
        # Regeneration: history truncation hook (consumed only by the supervisor
        # chain's _load_cluster_history; default None = legacy behavior)
        self._regenerate_before = regenerate_before

        # Same convention as streaming — the variable contract is handled once at the entry point and both modes share the rendered message.
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

        # Chat-feature runtime state (web search / deep thinking / opening statement / execution mode); all defaults = legacy behavior
        self._prepare_turn_chat_features(
            web_search, thinking, opening_statement, execution_mode, user_message_id
        )

        # Reset the token ledger each turn (same convention as streaming)
        self._turn_routing_tokens = 0
        self._turn_sub_tokens = 0
        self._turn_merge_tokens = 0

        try:
            # Collaboration mode: use handoffs_service
            if self._normalized_mode == OrchestrationMode.COLLABORATION:
                collab_result = await self._execute_collaboration_mode(
                    rendered_message,
                    conversation_id,
                    user_id,
                    cluster_variables.values or None,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                )
                # Non-streaming collaboration usage comes from handoffs'
                # internal accounting; it is merged into the ledger and
                # published uniformly (sub layer).
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

            # Supervisor Loop mode: supervisor = ReAct engine, sub-agents = tools.
            # No routing, no merging (final is the final answer); failures raise
            # directly, with no fallback to the three-stage path.
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
                    # Suggested questions / citations / audio: keys appear only when the corresponding features are on; existing clusters return unchanged
                    **(self._turn_extras or {}),
                }

            # Supervisor mode (three-stage): routing -> collective execution -> merging.
            # The loop previously toggled in from here; it has since been lifted
            # to a standalone mode, so this path has no more splitting.
            # 1. Master Agent analyzes the task and makes a decision
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

            # 2. Execute according to the Master Agent's decision
            results = await self._execute_conditional(
                task_analysis,
                conversation_id,
                user_id
            )

            # 3. Merge results
            final_result = await self._aggregate_results(results)

            elapsed_time = time.time() - start_time

            # 4. Total tokens (now read from the per-turn ledger — routing/sub/
            # merge are recorded inside _analyze_task /
            # _execute_sub_agent(_stream) / merge respectively; this spot no
            # longer manually extracts from results, which previously missed the
            # collaboration path and re-added sub usage already in the ledger —
            # one of the root causes of double counting).
            # No longer extract/return the sub-agent's conversation_id (that is
            # the sub-agent draft conversation's ID; conversation ownership for
            # multi-turn chats only follows the incoming conversation_id — the
            # caller passes it through to the next turn as-is; returning a
            # sub-conversation ID would steer later turns into a different
            # conversation).
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
                # Conversation ID belongs to the cluster conversation (incoming param passed through), no longer the sub-agent draft conversation
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
        """Master Agent analyzes the task and makes a routing decision.

        Args:
            message: User message
            variables: Variable parameters

        Returns:
            Task analysis result, including the routing decision
        """
        logger.info(
            "Master Agent 开始分析任务",
            extra={"message_length": len(message)}
        )

        # Use the Master Agent router to make the decision.
        # conversation_id comes from the instance attribute (written by
        # _ensure_master_execution at the execute/execute_stream entry point),
        # so routing state accumulates across turns — it used to be passed as
        # None, distorting "current Agent / consecutive rounds / switch_count".
        routing_decision = await self.router.route(
            message=message,
            conversation_id=(
                str(self.current_conversation_id) if self.current_conversation_id else None
            ),
            variables=variables
        )

        # Get tokens consumed by the routing decision
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
        """Execute sub-agents sequentially.

        Args:
            task_analysis: Task analysis result
            conversation_id: Conversation ID
            user_id: User ID

        Returns:
            List of execution results
        """
        results = []
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")

        # Sort by priority
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

            # Execute the sub-agent
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
                "conversation_id": result.get("conversation_id")  # Save conversation ID
            })

            # Update context (later Agents can use earlier results)
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
        """Execute sub-agents in parallel.

        Args:
            task_analysis: Task analysis result
            conversation_id: Conversation ID
            user_id: User ID

        Returns:
            List of execution results
        """
        context = task_analysis.get("initial_context", {})
        message = task_analysis.get("message", "")

        # Create the task list
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

        # The concurrency limit uses a semaphore (`_gather_limited`) instead of
        # batching with barriers — batching makes the second batch wait for the
        # slowest of the first, whereas the semaphore yields as soon as anyone
        # finishes.
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
                    "conversation_id": result.get("conversation_id")  # Save conversation ID
                })

        return results

    async def _execute_collaboration_stream(
        self,
        task_analysis: Dict[str, Any],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str],
        routing_decision: Dict[str, Any]
    ):
        """Multi-agent collaboration streaming execution.

        Args:
            task_analysis: Task analysis result
            conversation_id: Conversation ID
            user_id: User ID
            routing_decision: Routing decision

        Yields:
            A stream of SSE-formatted events
        """
        message = task_analysis.get("message", "")
        initial_context = task_analysis.get("initial_context", {})
        collaboration_strategy = routing_decision.get("collaboration_strategy", "sequential")

        # Get collaboration info
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

        # 1. Send the orchestration plan event (before execution)
        # Build subtask info
        sub_tasks = []
        for item in collaboration_agents:
            if collaboration_strategy == "decomposition":
                # Question-decomposition mode
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
                # Other collaboration modes
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

        # 2. Stream-execute all sub-agents
        results = []

        # Get the execution-mode config
        execution_mode = self._execution_config.get("sub_agent_execution_mode", "parallel")

        if collaboration_strategy == "decomposition":
            # Question-decomposition mode
            # Check whether there are dependencies
            has_dependencies = self._check_dependencies(collaboration_agents)

            if has_dependencies or execution_mode == "sequential":
                # Has dependencies or configured sequential: sequential streaming
                logger.info("使用串行流式执行（问题拆分）")
                for sub_q in sorted(collaboration_agents, key=lambda x: x.get("order", 0)):
                    sub_question = sub_q.get("question", "")
                    agent_id = sub_q.get("agent_id")

                    agent_data = self.sub_agents.get(agent_id)
                    if not agent_data:
                        continue

                    agent_name = agent_data.get("info", {}).get("name", agent_id)

                    # Send the sub-question start event
                    yield self._format_sse_event("sub_question_start", {
                        "question": sub_question,
                        "agent_name": agent_name
                    })

                    # Stream-execute the sub-agent and collect results
                    result_content = ""
                    async for event in self._execute_sub_agent_stream(
                        agent_data["config"],
                        sub_question,
                        initial_context,
                        conversation_id,
                        user_id
                    ):
                        # Parse the raw event
                        if "data:" in event:
                            try:
                                import json
                                data_line = event.split("data: ", 1)[1].strip()
                                data = json.loads(data_line)

                                # Extract content: accumulate only, do not send yet (emitted once in aggregate)
                                if "content" in data:
                                    result_content += data["content"]
                                elif _sse_event_name(event) in _CLUSTER_OBSERVABILITY_EVENTS:
                                    # Cluster observability events (tool timing / trace snapshots /
                                    # dispatch wrap-up) carry no content; they must be passed
                                    # through as-is with attribution added, otherwise the running
                                    # panel cannot see the sub-agent's call chain
                                    yield self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_name))
                            except Exception:
                                pass
                        else:
                            # Non-data events are forwarded directly
                            yield event

                    # Sub-agent body delivered in aggregate (sent only after this Agent finishes, full text arriving at once)
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

                    # Send the sub-question end event
                    yield self._format_sse_event("sub_question_end", {
                        "agent_name": agent_name
                    })
            else:
                # No dependencies and configured parallel: parallel streaming
                logger.info(f"使用并行流式执行（问题拆分），共 {len(collaboration_agents)} 个子问题")

                # Prepare parallel tasks
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

                    # Send the sub-question start event
                    yield self._format_sse_event("sub_question_start", {
                        "question": sub_question,
                        "agent_name": agent_name
                    })

                # Parallel streaming execution
                async for agent_id, agent_name, event_type, content in self._parallel_stream_agents(
                    agent_tasks,
                    conversation_id,
                    user_id
                ):
                    if event_type == "content":
                        # Accumulate result (do not send; emitted in aggregate when this Agent is done)
                        result_contents[agent_id] += content

                    elif event_type == "raw":
                        # Sub-agent observability events (tools / trace / dispatch wrap-up): passed through as-is
                        yield content

                    elif event_type == "done":
                        # Agent done: first deliver the body in aggregate (full text at once), then wrap up
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
                        # Partial body generated before the failure must still be delivered, otherwise aggregation would hide it entirely
                        if result_contents.get(agent_id):
                            yield self._sub_agent_message_event(
                                agent_id,
                                agent_name,
                                result_contents[agent_id],
                                sub_question=agent_info_map[agent_id]["sub_question"],
                            )
        else:
            # Other collaboration modes (sequential/parallel/hierarchical)
            if collaboration_strategy == "parallel" and execution_mode == "parallel":
                # Parallel collaboration + parallel streaming execution
                logger.info(f"使用并行流式执行（并行协作），共 {len(collaboration_agents)} 个 Agent")

                # Prepare parallel tasks
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

                    # Build this Agent's message
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

                    # Send the Agent start event
                    yield self._format_sse_event("agent_start", {
                        "agent_name": agent_name
                    })

                # Parallel streaming execution
                async for agent_id, agent_name, event_type, content in self._parallel_stream_agents(
                    agent_tasks,
                    conversation_id,
                    user_id
                ):
                    if event_type == "content":
                        # Accumulate result (do not send; emitted in aggregate when this Agent is done)
                        result_contents[agent_id] += content

                    elif event_type == "raw":
                        # Sub-agent observability events (tools / trace / dispatch wrap-up): passed through as-is
                        yield content

                    elif event_type == "done":
                        # Agent done: first deliver the body in aggregate (full text at once), then wrap up
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
                        # Partial body generated before the failure must still be delivered, otherwise aggregation would hide it entirely
                        if result_contents.get(agent_id):
                            yield self._sub_agent_message_event(
                                agent_id,
                                agent_name,
                                result_contents[agent_id],
                                role=agent_info_map[agent_id]["role"],
                            )
            else:
                # Sequential or hierarchical collaboration - sequential streaming execution
                logger.info(f"使用串行流式执行（{collaboration_strategy}）")
                for agent_info in collaboration_agents:
                    agent_id = agent_info.get("agent_id")
                    agent_data = self.sub_agents.get(agent_id)
                    if not agent_data:
                        continue

                    agent_name = agent_data.get("info", {}).get("name", agent_id)

                    # Send the Agent start event
                    yield self._format_sse_event("agent_start", {
                        "agent_name": agent_name
                    })

                    # Stream-execute the sub-agent and collect results
                    result_content = ""
                    async for event in self._execute_sub_agent_stream(
                        agent_data["config"],
                        message,
                        initial_context,
                        conversation_id,
                        user_id
                    ):
                        # Parse the raw event
                        if "data:" in event:
                            try:
                                import json
                                data_line = event.split("data: ", 1)[1].strip()
                                data = json.loads(data_line)

                                # Extract content: accumulate only, do not send yet (emitted once in aggregate)
                                if "content" in data:
                                    result_content += data["content"]
                                elif _sse_event_name(event) in _CLUSTER_OBSERVABILITY_EVENTS:
                                    # Same as the decomposition branch: pass observability events through as-is with attribution added
                                    yield self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_name))
                            except:
                                pass
                        else:
                            # Non-data events are forwarded directly
                            yield event

                    # Sub-agent body delivered in aggregate (sent only after this Agent finishes, full text arriving at once)
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

                    # Send the Agent end event
                    yield self._format_sse_event("agent_end", {
                        "agent_name": agent_name
                    })

        # 3. Smart result merging.
        # Default master: the Master Agent generates the final answer
        # **streaming**, so the frontend main bubble grows token by token.
        # smart is the fast "no model call" tier (pure concatenation) used only
        # as the fallbacks below, but it also pushes in chunks so no path ever
        # dumps the whole content at the frontend at once (otherwise the main
        # bubble would appear as one sudden lump).
        merge_mode = self._execution_config.get("result_merge_mode", "master")
        # Decide intelligently whether merging is needed
        need_merge = self._should_merge_results(results, collaboration_strategy)

        if not need_merge:
            # No LLM merge needed: the user already saw all output in the
            # individual blocks; here the combined content is only appended
            # into the main bubble in chunks (no model call)
            logger.info("跳过 Master 整合阶段（直接汇总各 Agent 输出）")
            self._set_merge_mode_actual("smart", "need_merge=False（单结果/策略判定不合并）")
            async for event in self._smart_merge_results_stream(results, collaboration_strategy):
                yield event
        elif merge_mode == "master" and len(results) > 1:
            # Master Agent streaming merge
            logger.info("开始 Master Agent 流式整合")
            self._set_merge_mode_actual("master")

            # Send the merge-start notice
            yield self._format_sse_event("merge_start", {
                "merge_mode": "master",
                "agent_count": len(results),
                "message": "正在整合多个专家的回答..."
            })

            # Streaming merge
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
            # Smart mode: fast merging without a model call (still streaming in chunks, so the main bubble does not appear as one lump)
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
    # supervisor_loop mode — supervisor control loop (ReAct-style, agents-as-tools)
    #
    # This section serves only orchestration_mode="supervisor_loop":
    # - supervisor (three-stage) no longer splits into the loop, and
    #   MasterAgentRouter/result_merge_mode/aggregation_strategy apply in full again;
    # - the loop does **not fall back** to the three-stage path: master model
    #   unavailable / no sub-agents / no body text raise BusinessException
    #   directly, and execute_stream/execute wrap up uniformly with a failed
    #   record and an error event.
    # ──────────────────────────────────────────────────────────────────

    def _build_supervisor_system_prompt(self) -> str:
        """Supervisor system prompt: user custom body (the supervisor is an Agent) + auto section (roster + dispatch discipline).

        The supervisor prompt used to be fully hardcoded (bare supervisor).
        From now on:
        - When `supervisor_config.system_prompt` is non-empty: after rendering
          cluster variables {{var}}, it is placed **before** the auto section
          (user intent takes priority; the auto section keeps dispatch
          discipline from being overridden);
        - Empty/unconfigured: identical to before (pure auto section), zero
          behavior change for existing clusters.

        Dispatch discipline: the motivation for delegating is **context
        isolation** rather than capability division of labor — exploratory /
        high-output tasks go to sub-agents to run in isolated context, while
        small conclusive questions are answered directly, keeping the main
        context from being flooded by tool output.
        """
        supervisor_config = self._supervisor_config_dict()
        custom_prompt = (supervisor_config.get("system_prompt") or "").strip()

        # Variable rendering: same as entry messages (replace only {{name}}
        # that actually exist in the variable bag); the supervisor and
        # sub-agents share the same variable bag (no supervisor-private
        # variables).
        _bag = getattr(self, "_cluster_variables", None)
        if custom_prompt and _bag is not None:
            custom_prompt = render_variables_in_text(custom_prompt, dict(_bag.values))

        # Supervisor skill prompt section (written when
        # _load_supervisor_own_tools loads skills; _build_supervisor_agent
        # guarantees tools are loaded before the prompt is assembled)
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

        # Own-tool guidance (conditionally generated from the supervisor_config
        # capability surface). Root cause: the model does not proactively call
        # tools the prompt never mentions — the auto section previously only
        # covered sub-agent dispatch, and "answer directly if you can" further
        # suppressed own tools (that is why the supervisor still skipped
        # long_term_memory after the user enabled the memory switch). Agent apps
        # rely on the user's own prompt for guidance; the supervisor auto
        # section is system-generated, so the system must fill the guidance in.
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

        # Final answer format constraint: with json_output on, the supervisor
        # always carries tools, so response_format is removed (compatible_chat /
        # openai-compatible providers) and deep thinking also skips it; JSON can
        # only be enforced by the prompt. The former fixed "reply in Chinese"
        # text conflicts with the engine's appended JSON instruction, producing
        # natural language and silently disabling json_output.
        if self._supervisor_json_output_on():
            final_answer_rule = (
                "最终答案输出格式（强制）：调用工具/子 Agent 的过程不受限制，但最终答案必须且只能是"
                "一个合法的 JSON 对象（以 { 开头、以 } 结尾），JSON 内的文字内容使用中文；"
                "不要输出 Markdown 代码块（```）、不要在 JSON 前后附加任何说明文字，不要提及调度过程。"
            )
        else:
            final_answer_rule = "最终答案必须是完整、自洽的中文回复，不要提及调度过程。"

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
            f"{final_answer_rule}"
        )
        if not self.sub_agents:
            # No sub-agents: the supervisor is a bare agent. The standard prompt above
            # advertises "available sub-agents" (an empty roster), which misleads the
            # model into hallucinating tools; the own-tools guidance is preserved.
            auto_prompt = (
                "你是多智能体集群的主管（supervisor），当前没有配置子 Agent，"
                "请直接回答用户问题。"
                f"{own_tools_section}\n\n"
                f"{final_answer_rule}"
            )
        if not custom_prompt:
            return auto_prompt
        return f"{custom_prompt}\n\n---\n\n{auto_prompt}"

    def _supervisor_json_output_on(self) -> bool:
        """Whether the cluster's model_parameters.json_output is on (same read as _build_supervisor_agent)."""
        mp = self.model_parameters
        if mp is None:
            return False
        val = mp.get("json_output", False) if isinstance(mp, dict) else getattr(mp, "json_output", False)
        return bool(val)

    def _supervisor_skill_prompt_section(self) -> str:
        """Supervisor skill prompt section: written when _load_supervisor_own_tools loads skills.

        Same approach as Agent apps (skill_prompts appended to system_prompt).
        _build_supervisor_agent's call order guarantees tools load before the
        prompt is assembled.
        """
        prompts = (getattr(self, "_loop_skill_prompts", "") or "").strip()
        return prompts

    def _supervisor_config_dict(self) -> Dict[str, Any]:
        """Safe read of supervisor_config (the column may be NULL / dirty / dict-shaped).

        The supervisor as Agent: the capability surface of supervisor_loop mode.
        Any unexpected shape falls back to an empty dict (= bare supervisor as
        before) and never blocks execution — configuration-surface issues must
        not blow up the runtime.
        """
        raw = self._supervisor_config_snapshot
        if not isinstance(raw, dict):
            return {}
        return raw

    async def _resolve_supervisor_model_view(self) -> "ModelInfo":
        """Non-decrypted runtime view of the supervisor model (credentials are not shipped up; routing and credential availability are decided by the model service).

        Default model unconfigured / missing / deprecated -> pre-check failure
        raises (stop reason recorded as error, no fallback to other modes).
        """
        try:
            if not self.default_model_config_id:
                raise BusinessException(
                    "主管循环模式需要可用的主模型（默认模型未配置）",
                    BizCode.AGENT_CONFIG_MISSING,
                )
            return await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db, self.default_model_config_id, tenant_id=self.tenant_id
            )
        except BusinessException:
            self._loop_stop_reason = "error"
            raise

    async def _resolve_supervisor_tts_dict(self) -> Dict[str, Any]:
        """Existing consumer that needs plaintext for local TTS synthesis (an exception): channel-resolution shell -> dict; failures raise precise errors."""
        api_key_obj = await ModelApiKeyService.resolve_runtime_api_key_bridge_or_raise_async(
            self.db, self.default_model_config_id, tenant_id=self.tenant_id
        )
        return self._supervisor_api_key_dict(api_key_obj)

    def _resolve_loop_max_iterations(self) -> int:
        """Resolve the **supervisor loop round** limit for supervisor_loop mode (execution_config.max_iterations).

        Semantics: the maximum number of rounds the supervisor ReAct loop may
        turn; one round = the batch of sub-agent calls issued by one supervisor
        decision (N concurrent calls in the same round count as 1). Soft-limited
        by the round gate in `SubAgentTool._execute`: once the limit is reached,
        calls in the new round are not executed and the limit is written into
        the tool return value so the supervisor wraps up based on existing
        results. It is no longer converted into LangGraph recursion_limit.

        - Convertible to int and >= 1 -> return that value;
        - Unconfigured / empty / dirty (0, negative, non-numeric) -> return the
          schema default of ExecutionConfig.max_iterations (10).
        This parameter is no longer user-configurable and is normally always the
        default.
        """
        from ..schemas.multi_agent_schema import ExecutionConfig

        default = ExecutionConfig.model_fields["max_iterations"].default
        cfg = self._execution_config or {}
        try:
            val = int(cfg.get("max_iterations"))
        except (TypeError, ValueError):
            return default
        return val if val >= 1 else default

    async def _build_supervisor_agent(self, model_view: "ModelInfo"):
        """Build the supervisor LangChainAgent (tools = supervisor's own tools + SubAgentTool x N).

        The supervisor as Agent: the supervisor tool surface and sub-agent tools
        merge into a single list (isomorphic to LangChain
        create_supervisor(agents, tools=[...])).

        All guards use existing engine parameters (nothing custom):
        - tool_call_limit: execution_config.supervisor_max_tool_calls (default 3)
          — the N+1th call of the same sub-agent is rejected and the model is
          told to give a final answer (no-progress detection);
        - max_iterations: execution_config.max_iterations — the supervisor loop
          round limit, soft-limited by the round gate in SubAgentTool._execute
          (the limit is written into the tool return value for the supervisor
          to wrap up); the engine's recursion_limit is always passed None and
          computed dynamically from the tool count, serving only as a
          hard-truncation backstop;
        - GraphRecursionError: handled by the engine's internal graceful
          degradation.
        """
        from ..agent.langchain_agent import LangChainAgent

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

        # max_iterations semantics = supervisor loop round limit (one round = the
        # batch of sub-agent calls issued by one supervisor decision; concurrent
        # calls in the same batch count as 1), soft-limited by the round gate in
        # SubAgentTool._execute: once reached, the limit is written into the tool
        # return value so the supervisor wraps up based on existing results (same
        # mechanism as the engine's tool_call_limit). The engine's LangGraph
        # recursion_limit is only a backstop: passing None lets the engine
        # compute 5 + tool_count x tool_call_limit x 2 dynamically, which is
        # necessarily no smaller than the maximum steps the soft limit can
        # allow, so hard truncation (GraphRecursionError -> the engine swallows
        # it into a generic apology) never fires before the soft limit.
        loop_max_iterations = None

        tools = []
        self._loop_tool_instances = []
        _used_tool_names: set = set()
        for agent_id, data in self.sub_agents.items():
            info = data.get("info", {})
            wrapper = SubAgentTool(
                self,
                agent_id,
                info.get("name", agent_id),
                _sub_agent_brief(data),
            )
            # Tool-name de-duplication: _sanitize_tool_name replaces non-ASCII
            # characters with underscores, so sub-agent names distinguished only
            # by Chinese text (e.g. "extract / calculate / review") collapse to
            # the same tool name; LangChain then keeps only the last one by name
            # and the supervisor can only ever call it. Append an agent-id suffix
            # on conflict (the original name stays in the description).
            if wrapper.tool_name in _used_tool_names:
                _suffix = str(agent_id).replace("-", "")[:8] or "x"
                _candidate = f"{wrapper.tool_name[:55]}_{_suffix}"
                _n = 2
                while _candidate in _used_tool_names:
                    _candidate = f"{wrapper.tool_name[:50]}_{_suffix}_{_n}"
                    _n += 1
                wrapper.tool_name = _candidate
            _used_tool_names.add(wrapper.tool_name)
            self._loop_tool_instances.append(wrapper)
            tools.append(wrapper.build())

        # Supervisor's own tool surface (merged with SubAgentTool x N into one list).
        # Reuses the Agent-app build pipeline (AgentRunService's load_* methods) —
        # same capabilities as single-Agent apps (tools/skills/knowledge/memory).
        # A failed build degrades to a bare supervisor (only SubAgentTool) without
        # blocking the conversation — the capability surface is additive, and
        # config/environment issues must not blow up the runtime.
        #
        # Boundary: the supervisor tool surface stays "high-level"; users decide
        # what to configure. Web search is not supported on the supervisor for
        # now (SupervisorConfig has no web_search; load_tools_config keeps it off).
        own_tools = await self._load_supervisor_own_tools()
        if own_tools:
            tools = own_tools + tools

        # Emotion sensing (features.emotion_reply): await the detection result
        # and inject it into the supervisor prompt; when off,
        # _turn_emotion_detection is None and the prompt is returned as-is, so
        # existing clusters are unaffected.
        _supervisor_prompt = self._build_supervisor_system_prompt()
        if self._turn_emotion_detection is not None:
            try:
                from ..memory.emotion.emotion_resolver import apply_detection

                _supervisor_prompt = await apply_detection(
                    _supervisor_prompt,
                    self._turn_emotion_detection,
                    self._turn_user_message_id,
                    app_id=self._app_id,
                    tenant_id=self.tenant_id,
                    db=self.db,
                    write_cache=self.cluster_memory_enabled(),
                )
            except Exception as e:  # noqa: BLE001 - emotion sensing failure must not block the conversation
                logger.warning(f"集群情绪注入失败（已跳过）: {e}")
            finally:
                self._turn_emotion_detection = None

        # Context engine (features.context_engine): off / plugin missing /
        # exception all return the prompt as-is, and history falls back to the
        # cluster's 20-turn history in _resolve_loop_history; existing clusters
        # are unchanged.
        _supervisor_prompt = await self._prepare_context_engine(
            getattr(self, "current_conversation_id", None), _supervisor_prompt, model_view
        )

        # Remote mode: identity/capability come from the non-decrypted view
        # (api_key/api_base always empty); credential decryption, routing and
        # channel switching all live in the model service (consistent with
        # Agent-app construction).
        return LangChainAgent(
            model_name=model_view.model_name,
            model_view=model_view,
            provider=model_view.provider or "openai",
            temperature=_get("temperature", 0.7),
            max_tokens=_get("max_tokens", 4096),
            system_prompt=_supervisor_prompt,
            tools=tools,
            streaming=True,
            tool_call_limit=max(1, tool_call_limit),
            # Explicit config overrides the engine's dynamic value; None = the engine computes it from the tool count
            max_iterations=loop_max_iterations,
            # Deep thinking: enabled only when model_parameters.deep_thinking and
            # the request's thinking are both true (resolved by
            # _prepare_turn_chat_features); default False/None = same as before.
            # json_output: model_parameters.json_output (a frontend model-config
            # switch; the model must support the json_output capability, and the
            # engine normalizes by model capability); default False = same as before.
            json_output=bool(_get("json_output", False)),
            deep_thinking=bool(getattr(self, "_loop_deep_thinking", False)),
            thinking_budget_tokens=(
                _get("thinking_budget_tokens", None)
                if getattr(self, "_loop_deep_thinking", False)
                else None
            ),
        )

    async def _load_supervisor_own_tools(self) -> list:
        """Load the supervisor's own tool surface (tools/skills/knowledge/memory; no web search).

        Reuses AgentRunService's build pipeline (load_tools_config /
        load_skill_config / load_knowledge_retrieval_config) — same origin as
        single-Agent apps, zero divergence in capability shape. Supervisor
        skill prompts (skill_prompts) are appended into the supervisor
        system_prompt (same approach as Agent apps).

        Failure semantics: any failed segment only logs a warning and is skipped
        (tool surface degrades) without raising — supervisor tools are
        additive, and environment issues (tenant has no tools / deleted
        knowledge base) must not make the cluster conversation unusable.

        Returns:
            list: LangChain tool list (may be empty = bare supervisor with only SubAgentTool).
        """
        supervisor_config = self._supervisor_config_dict()
        if not supervisor_config:
            return []

        # Runtime identity for supervisor tools: the cluster run user (falls
        # back to "anonymous", consistent with the runtime_context attached to
        # sub-agent tools).
        user_id = getattr(self, "_loop_user_id", None) or "anonymous"
        conversation_id = getattr(self, "current_conversation_id", None)

        from .draft_run_service import AgentRunService

        run_svc = AgentRunService(self.db)
        tools: list = []

        # 1. Regular tools + supervisor web search (enabled only when
        #    features.web_search.enabled and web_search requested; existing
        #    clusters have no features -> False, same as before)
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

        # 2. Skills (tools + skill prompt; the prompt is appended into system_prompt by the caller)
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

        # 3. Knowledge retrieval. citations_collector is stored into
        # self._loop_citations and only outputs the supervisor's own
        # knowledge-base citations when features.citation is on; sub-agent
        # citations are not aggregated (the supervisor only absorbs the
        # sub-agents' returned content and does not care about their internal
        # retrieval).
        try:
            knowledge_config = supervisor_config.get("knowledge_retrieval")
            if knowledge_config and isinstance(knowledge_config, dict):
                from ..integrations.knowledge.contracts import KnowledgeRetrievalSource

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
                    # The citation collector is filled in place during retrieval tool calls; at the end the citation switch filters the output
                    self._loop_citations = _citations if _citations is not None else []
        except Exception as e:
            logger.warning(f"主管知识库加载失败（已跳过知识库段）: {e}")

        # 4. Long-term memory (memory.enabled) — same memory tools as Agent apps
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
                        app_id=self._app_id,
                    )
                    tools.extend(memory_tools)
        except Exception as e:
            logger.warning(f"主管长期记忆加载失败（已跳过记忆段）: {e}")

        # Attach tool runtime context (same as Agent apps: user/conversation targeting)
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
        """The cluster's workspace (used for supervisor tool runtime context).

        Goes through _db_get (compatible with both sync and async sessions;
        must be awaited). Returns None when unavailable — the load_* pipelines
        fall back on their own for a None workspace; but the knowledge/memory
        segments are skipped entirely when workspace is missing, so read
        failures must leave a warning and never be silent.
        """
        app_id = self._app_id
        if app_id and self.db is not None:
            try:
                from ..models import App

                app = await self._db_get(App, app_id)
                if app is not None and app.workspace_id:
                    return app.workspace_id
            except Exception as e:
                logger.warning(f"主管工具：读取集群 workspace 失败（忽略）: {e}")
        return None

    def cluster_memory_enabled(self) -> bool:
        """Cluster memory switch (long-term memory) — the single source of truth: supervisor_config.memory.enabled.

        All orchestration modes, all sub-agent execution paths, and whether this
        turn's messages are written to memory (should_memorize) follow it,
        **no longer affected by the request-level memory parameter**: cluster on
        -> sub-agents may carry memory (each sub-agent's own memory.enabled must
        still be on); cluster off (including unconfigured/dirty) -> no memory at
        all.

        Cluster conversation history (_load_cluster_history, 20 turns) is not
        controlled by this switch and is always kept — it is cluster
        conversation semantics, not a capability switch.
        """
        memory_cfg = self._supervisor_config_dict().get("memory")
        return isinstance(memory_cfg, dict) and bool(memory_cfg.get("enabled"))

    async def _load_cluster_history(
        self,
        conversation_id: Optional[uuid.UUID],
        db=None,
    ) -> List[Dict[str, Any]]:
        """Cluster conversation history (supervisor multi-turn memory).

        In loop mode the supervisor carries the cluster conversation messages
        every turn (excluding the assistant message currently being generated
        this turn — persistence goes through BatchPersistQueue and has not
        landed yet). Read failures degrade to an empty list (the conversation is
        not interrupted).
        """
        db = self.db if db is None else db
        if not conversation_id or db is None:
            return []
        try:
            # Regeneration scenario (when _regenerate_before is set): truncate by the
            # original question's created_at so the supervisor only sees context before
            # the regenerated round, same convention as single-agent regenerate
            # (AppChatService._load_history_before_message's before_time semantics).
            # get_conversation_history does not expose created_at, so query Message directly.
            if self._regenerate_before is not None:
                from sqlalchemy import select

                from ..models import Message

                stmt = (
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.is_current.is_not(False),
                        Message.is_deleted.is_not(True),
                        Message.created_at < self._regenerate_before,
                    )
                    .order_by(Message.created_at.asc())
                )
                if isinstance(db, AsyncSession):
                    rows = (await db.execute(stmt)).scalars().all()
                else:
                    rows = db.scalars(stmt).all()
                return [
                    {"role": m.role, "content": m.content}
                    for m in (rows or [])
                    if isinstance(m.content, str)
                ]

            from .conversation_service import ConversationService

            conv_service = ConversationService(db)
            history = await conv_service.get_conversation_history(
                conversation_id, max_history=20,
            )
            # Keep only plain-text turns (multimodal content is a list; the supervisor chain does not support it)
            return [
                {"role": h["role"], "content": h["content"]}
                for h in (history or [])
                if isinstance(h.get("content"), str)
            ]
        except Exception as e:
            logger.warning(f"S8 loop：读取集群会话历史失败（降级为空）: {e}")
            return []

    async def _sub_agent_history(
        self,
        conversation_id: Optional[uuid.UUID],
        db=None,
    ) -> List[Dict[str, Any]]:
        """History a sub-agent runtime carries.

        - supervisor_loop: the supervisor holds the cluster history itself and
          sub-agents only see the task brief + variable bag, so the history is
          empty (by design, to keep supervisor messages and a sub-agent's
          previous turn out).
        - supervisor (three-stage): the router only picks the agent and the
          sub-agent answers the user directly; passing no history makes every
          turn a fresh conversation (multi-turn corrections, "remember this
          order" all fail). Here the cluster history is passed (same source as
          the supervisor loop: 20 turns, plain text, regeneration truncated at
          the original question time; this turn is not persisted yet, so no
          duplication).
        - Other modes keep the previous behavior (empty history).
        """
        if self._normalized_mode == OrchestrationMode.SUPERVISOR:
            return await self._load_cluster_history(conversation_id, db=db)
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
        """Supervisor loop (streaming): supervisor ReAct engine + SubAgentTool bridge.

        Only invoked for orchestration_mode="supervisor_loop" (promoted from an
        internal supervisor switch to a standalone mode).

        Event surface (frontend clusterStream.ts needs zero changes):
        - Sub-agents: agent_dispatch / agent_log / agent_complete (pushed back via
          the event_sink queue);
        - Supervisor text chunk -> message (main bubble grows token by token);
        - Supervisor tokens -> _turn_routing_tokens (routing token ledger; published
          uniformly in the end event).

        Concurrency structure (critical): the supervisor engine's astream_events and
        "sub-agent tool execution" run on the same event loop — the engine is suspended
        while a tool runs. Draining the sub-agent event queue only in the gaps between
        engine yields would make the frontend wait for the whole sub-agent run before
        seeing dispatch/log/complete, which defeats the point of a "running" panel. So
        the supervisor stream goes into its own task and merges with the sub-agent event
        pump into one queue; this generator only does "ordered consumption + categorized
        dispatch".

        Failure semantics (**no fallback to the three-stage mode**): main model
        unavailable / no sub-agents / exception with no content / zero final content ->
        raise BusinessException, handled by execute_stream's unified except (records a
        failed execution + error event). Exceptions after partial content keep the
        output and raise nothing.
        """
        # Sub-agent execution parameters (read by the SubAgentTool execution body)
        self._loop_user_id = user_id
        # Sub-agent web search: pure request-level passthrough (session-level switch on
        # the experience-sharing entry). The supervisor itself offers no search config
        # (SupervisorConfig has no web_search)
        self._loop_web_search = bool(web_search)
        self._loop_memory = self.cluster_memory_enabled()
        self._loop_storage_type = storage_type
        self._loop_user_rag_memory_id = user_rag_memory_id
        self._loop_stop_reason = None
        # Entry message for this turn (used by load_skill_config for supervisor skill
        # filtering; same convention as Agent apps)
        self._loop_entry_message = message

        # Sub-agents may be empty: the supervisor then acts as a bare agent (own tools /
        # knowledge only) instead of being rejected here. A missing supervisor model is
        # still enforced by _resolve_supervisor_model_view below.
        model_view = await self._resolve_supervisor_model_view()

        self._set_merge_mode_actual("loop", "supervisor_loop 模式（主管 ReAct 循环）")

        # Emotion detection: a background recognition task starts only when
        # features.emotion_reply is on (concurrent with building the supervisor); its
        # result is injected into the supervisor prompt in _build_supervisor_agent; None
        # when off, zero overhead.
        self._start_turn_emotion_detection(message)
        supervisor = await self._build_supervisor_agent(model_view)
        # History: use context_engine's result when active this turn, otherwise the
        # cluster's 20-turn history; then prepend the opening line (returned as-is when
        # there is none)
        history = await self._resolve_loop_history(conversation_id)
        # File upload (features.file_upload): with no files, llm_message=message and
        # processed_files=None, unchanged from before
        llm_message, processed_files = await self._prepare_loop_files(
            files, model_view, message, supervisor
        )

        # Sub-agent observability event queue: SubAgentTool execution body -> supervisor
        # loop -> SSE. Wiring goes through the _loop_tool_instances instance list
        # (populated by _build_supervisor_agent) instead of reverse-looking up LangChain
        # tool objects: the engine's _wrap_tools_with_call_limit wraps _run/_arun in
        # place, so recovering the closure self from the tool object is unreliable.
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
            """Supervisor engine stream -> merged queue (exceptions become an error marker, never crash the task)."""
            try:
                async for item in supervisor.chat_stream(
                    message=llm_message, history=history or None, files=processed_files or None
                ):
                    await merged.put(item)
            except Exception as exc:  # noqa: BLE001 - all funneled to the fallback decision below
                await merged.put(("error", exc))
            finally:
                await merged.put(_DONE)

        async def _pump_sub_events():
            """Sub-agent event pump: the tool execution body posts anytime; forward to merged immediately."""
            while True:
                sse_event = await event_queue.get()
                if sse_event:
                    await merged.put(sse_event)

        producer = asyncio.ensure_future(_produce_supervisor())
        pump = asyncio.ensure_future(_pump_sub_events())

        final_content = ""
        supervisor_tokens = 0
        loop_error: Optional[BaseException] = None

        # Streaming TTS (created only when features.text_to_speech is on; all three are
        # None when off, zero overhead). Fed only with the supervisor's final content
        # chunks, never sub-agent content.
        tts_queue, tts_audio_url, tts_task = await self._setup_turn_tts()

        try:
            while True:
                item = await merged.get()
                if item is _DONE:
                    break
                if isinstance(item, tuple) and len(item) == 2 and item[0] == "error":
                    loop_error = item[1]
                    break
                # Sub-agent observability event: the whole SSE string goes out as-is
                # (carries its own execution_id attribution)
                if isinstance(item, str) and item.startswith("event:"):
                    yield item
                    continue
                # LangChainAgent.chat_stream yield type contract (engine comment at :1019):
                # str=content chunk; int=token; dict=agent_log/reasoning/tool_*;
                # list=node_executions
                if isinstance(item, str):
                    final_content += item
                    if tts_queue is not None:
                        tts_queue.put_nowait(item)
                    yield self._format_sse_event("message", {"content": item})
                elif isinstance(item, int):
                    supervisor_tokens = max(supervisor_tokens, int(item))
                elif isinstance(item, dict):
                    # Forward the supervisor's own tool calls (product definition: the
                    # run panel shows "tool calls and sub-agent calls" — supervisor-owned
                    # tools are the former). The engine yields one dict each time
                    # on_tool_start/tool_end/tool_error fires in chat_stream
                    # (langchain_agent.py:1214/:1288/:1327); previously the whole dict
                    # branch only recorded tokens, so tool calls were invisible on the
                    # panel. SubAgentTool is not forwarded: sub-agent calls already have
                    # the dedicated agent_dispatch/agent_log/agent_complete blocks (events
                    # are pushed back by the tool execution body via event_sink);
                    # forwarding here would show the same call twice.
                    _item_type = item.get("type")
                    # Deep-thinking content: the engine produces reasoning only when
                    # deep_thinking is active; older clusters (not enabled) never see this
                    # event, so the contract is unchanged. Format matches the Agent app.
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
                    # Supervisor traces are not emitted to the cluster SSE (product
                    # definition: the run panel only shows tool calls and sub-agent calls;
                    # the supervisor's scheduling reasoning is not a "call"). Tokens must
                    # be taken here: the engine finally yields only the last LLM call's
                    # token count, and trace.finalize overwrites meta.total_tokens, so
                    # multi-round loops lose accounting; iterations[].llm.tokens
                    # accumulates per round — use it to align with the routing ledger.
                    _trace_tokens = _trace_total_tokens(item)
                    if _trace_tokens:
                        supervisor_tokens = max(supervisor_tokens, _trace_tokens)
                # list (node_executions) is ignored: step attribution lives in the
                # execution records
        finally:
            pump.cancel()
            if not producer.done():
                producer.cancel()
            else:
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass

        # Drain leftover sub-agent queue items (including the tool's fallback failed
        # closure) so frontend blocks never stay stuck in running. Kept outside the try:
        # yielding inside finally, when the consumer disconnects early (GeneratorExit),
        # raises "async generator ignored GeneratorExit".
        while not event_queue.empty():
            try:
                _pending = event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if _pending:
                yield _pending

        self._loop_stop_reason = self._resolve_loop_stop_reason(loop_error)

        # Supervisor token accounting (routing layer = supervisor decisions and
        # generation; sub layer = sub-agents)
        self._turn_routing_tokens += int(supervisor_tokens or 0)

        # Content finished: close the TTS queue and produce the extra end fields
        # (suggested questions / citations / audio). Each field only has a value when its
        # feature is on; nothing is produced when there is no content (an error follows).
        if tts_queue is not None:
            tts_queue.put_nowait(None)
        if final_content.strip():
            await self._finalize_turn_extras(
                final_content, model_view, audio_url=tts_audio_url, tts_task=tts_task
            )

        if loop_error is not None:
            logger.error(
                "S9 supervisor_loop：主管循环执行失败",
                extra={"error": str(loop_error), "stop_reason": self._loop_stop_reason},
                exc_info=loop_error,
            )
            if final_content:
                # Partial output exists: keep it for the user; raise nothing and do not
                # rerun (duplicate cost + divergent answers). The frontend already got the
                # content, so the closing end event proceeds as usual.
                self._set_merge_mode_actual(
                    "loop", f"loop 异常（保留部分输出）: {str(loop_error)[:200]}"
                )
                return
            # Zero-output exception raises immediately (no three-stage fallback — mode
            # semantics stay predictable and errors attributable)
            self._set_merge_mode_actual("loop", f"loop 异常: {str(loop_error)[:200]}")
            raise BusinessException(
                f"主管循环执行失败: {str(loop_error)[:200]}",
                BizCode.LLM_ERROR,
            )

        # The engine ended normally yet produced nothing (model only called tools without
        # wrapping up / the engine swallowed output after GraphRecursionError): no longer
        # handed to the three-stage fallback — an empty answer is a silent failure for the
        # user, so raising lets both the frontend and the execution record attribute it.
        if not final_content.strip():
            logger.warning("S9 supervisor_loop：主管未产出正文")
            self._set_merge_mode_actual("loop", "loop 无正文产出")
            raise BusinessException(
                "主管循环未产出任何回答（可能已达分派/轮数护栏仍未收尾），请重试或调整"
                " supervisor_max_tool_calls；需要确定性编排可改用主管模式（三段式）",
                BizCode.LLM_ERROR,
            )

    def _resolve_loop_stop_reason(self, loop_error: Optional[BaseException]) -> str:
        """Determine why the loop ended this turn (end event loop_stop_reason field; makes guardrails observable).

        - direct_answer: the supervisor answered directly (zero dispatch) — a core gain of loop mode;
        - final_after_dispatch: normal wrap-up after dispatching;
        - tool_call_limit: a sub-agent hit the supervisor_max_tool_calls cap and was rejected (no-progress detection);
        - max_iterations: the supervisor loop hit max_iterations (round gate, see SubAgentTool._execute),
          or the engine raised GraphRecursionError (hard-truncation fallback);
        - error: any other exception.
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
        """Supervisor loop (non-streaming): same semantics as the streaming version (engine chat()).

        Only invoked for orchestration_mode="supervisor_loop". **No three-stage
        fallback**: main model unavailable / no sub-agents / no content / exception ->
        raise BusinessException, handled by execute()'s unified except (records a failed
        execution).

        Returns:
            dict: {content, usage, loop_stop_reason} (loop finished normally).
        """
        self._loop_user_id = user_id
        # Sub-agent web access: request-level passthrough (web_search defaults to False;
        # existing callers unchanged)
        self._loop_web_search = bool(web_search)
        self._loop_memory = self.cluster_memory_enabled()
        self._loop_storage_type = storage_type
        self._loop_user_rag_memory_id = user_rag_memory_id
        self._loop_stop_reason = None
        self._loop_entry_message = message

        # Sub-agents may be empty (same as the streaming path): the supervisor degrades
        # to a bare agent answering directly instead of being rejected here.
        model_view = await self._resolve_supervisor_model_view()

        self._set_merge_mode_actual("loop", "supervisor_loop 模式（主管 ReAct 循环）")

        # Emotion detection: a background recognition task runs only when
        # features.emotion_reply is on; None when off, zero overhead
        self._start_turn_emotion_detection(message)
        supervisor = await self._build_supervisor_agent(model_view)
        # History: use context_engine's result when active this turn, otherwise the
        # cluster's 20-turn history; then prepend the opening line
        history = await self._resolve_loop_history(conversation_id)
        # File upload (features.file_upload): with no files, llm_message=message and
        # processed_files=None, unchanged from before
        llm_message, processed_files = await self._prepare_loop_files(
            files, model_view, message, supervisor
        )

        # Non-streaming loop: the sink is not attached (no SSE outlet), so sub-agent
        # events are dropped; the execution tree is still persisted inside run_stream, so
        # the log view is not missing data. Call counts are still tracked (needed for the
        # loop_stop_reason decision).
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
            # Non-streaming usage likewise covers only the last LLM call (engine
            # semantics) and under-counts multi-round loops; the streaming path corrects
            # this by accumulating trace per round, but the engine emits no trace here, so
            # keep last-call semantics and leave a log trail (ledger semantics differ
            # from streaming).
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
            # Suggested questions / citations / TTS are produced only when the
            # corresponding feature is on (off by default = no new keys)
            await self._finalize_turn_extras(
                content,
                model_view,
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
            # Business errors (including the "no content" one above) propagate as-is;
            # execute() wraps up uniformly
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
        """Conditional routing execution (streaming, refactored - uses Master Agent decisions)

        Args:
            task_analysis: Task analysis result (contains the Master Agent's decision)
            conversation_id: Conversation ID
            user_id: User ID

        Yields:
            SSE-formatted event stream
        """
        if not task_analysis["sub_agents"]:
            raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

        # Note: this method only carries the supervisor three-stage mode
        # (supervisor_loop runs its ReAct loop in a separate execute_stream branch and
        # never reaches here).

        message = task_analysis.get("message", "")
        routing_decision = task_analysis.get("routing_decision")
        yield self._format_sse_event("routing_decision", {
            "routing_decision": routing_decision
        })

        # 1. Check whether collaboration is needed
        if routing_decision and routing_decision.get("need_collaboration"):
            # Multi-agent collaboration needed; use streaming merge
            logger.info("检测到需要多 Agent 协作，使用流式整合")

            async for event in self._execute_collaboration_stream(
                task_analysis,
                conversation_id,
                user_id,
                routing_decision
            ):
                yield event
            return

        # 2. Single-agent mode: use the Master Agent's decision directly when present
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
            # 2. Fallback: use the legacy routing logic
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

        # 3. Load agent configuration
        agent_data = self.sub_agents.get(agent_id)
        if not agent_data:
            raise BusinessException(f"子 Agent 不存在: {agent_id}", BizCode.AGENT_CONFIG_MISSING)

        agent_info = agent_data.get("info", {})

        # 4. Emit the routing information event
        yield self._format_sse_event("agent_selected", {
            "agent_id": agent_id,
            "agent_name": agent_info.get("name"),
            "routing_decision": {
                "confidence": routing_decision.get("confidence") if routing_decision else None,
                "reasoning": routing_decision.get("reasoning") if routing_decision else None,
                "strategy": routing_decision.get("strategy") if routing_decision else None
            }
        })

        # 5. Stream the sub-agent execution
        # Routing tokens go into the ledger (already extracted from
        # router._last_routing_tokens by _analyze_task) and are no longer sent upstream
        # as a sub_usage event — the ledger is published uniformly in the end event and
        # the upstream layer no longer parses SSE strings to accumulate.
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
            # Sub-run lifecycle events (start/end) are not passed through: otherwise the
            # upstream layer would see two starts / two ends, and the sub-run's temporary
            # message_id would displace the cluster bubble's real id (see
            # _SUB_RUN_LIFECYCLE_EVENTS).
            if _sse_event_name(event) in _SUB_RUN_LIFECYCLE_EVENTS:
                continue

            # Other events pass through as-is; sub_usage is already recorded in the
            # ledger inside _execute_sub_agent_stream and is no longer passed through;
            # first strip the sub-run's own conversation_id (a sub-agent draft
            # conversation ID would poison the frontend's conversation pointer and make
            # later turns hit another conversation), then add the agent attribution (only
            # fill missing fields, never overwrite same-named fields carried by the
            # event) so the running panel can route events to the matching sub-agent block.
            yield self._inject_agent_meta(
                self._strip_conversation_id(event),
                self._sub_event_meta(agent_id, agent_info.get("name")),
            )

        # 6. The conversation ID is already published uniquely as the cluster
        # conversation ID by this orchestrator's own start/end events, so no conversation
        # event goes out here (the old logic forwarded the sub-agent draft conversation
        # ID; once the frontend adopted it, later turns wrote to another conversation —
        # one root cause of conversation splitting).

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
        """Conditional routing execution (refactored - uses Master Agent decisions)

        Args:
            task_analysis: Task analysis result (contains the Master Agent's decision)
            conversation_id: Conversation ID
            user_id: User ID

        Returns:
            Execution result
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

        # Check whether collaboration is needed
        if routing_decision.get("need_collaboration"):
            collaboration_strategy = routing_decision.get("collaboration_strategy", "sequential")

            # Resolve collaboration info based on the strategy
            if collaboration_strategy == "decomposition":
                # Question-decomposition mode: use sub_questions
                collaboration_agents = routing_decision.get("sub_questions", [])
                logger.info(
                    "Master Agent 建议问题拆分",
                    extra={
                        "sub_question_count": len(collaboration_agents),
                        "strategy": collaboration_strategy
                    }
                )
            else:
                # Other collaboration modes: use collaboration_agents
                collaboration_agents = routing_decision.get("collaboration_agents", [])
                logger.info(
                    "Master Agent 建议多 Agent 协作",
                    extra={
                        "collaboration_agent_count": len(collaboration_agents),
                        "strategy": collaboration_strategy
                    }
                )

            # Execute multi-agent collaboration
            return await self._execute_collaboration(
                message=message,
                collaboration_agents=collaboration_agents,
                strategy=collaboration_strategy,
                initial_context=task_analysis.get("initial_context", {}),
                conversation_id=conversation_id,
                user_id=user_id,
                routing_decision=routing_decision
            )

        # 3. Load agent configuration
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

        # 4. Execute the agent
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

        # 5. Return the result
        return {
            "agent_id": agent_id,
            "agent_name": agent_info.get("name"),
            "result": result,
            "conversation_id": result.get("conversation_id"),
            "routing_decision": routing_decision  # contains the Master Agent's decision info
        }

    # The former `_execute_loop` (iterative-optimization loop) was deleted: it had zero
    # callers anywhere in the repo and was a leftover from the early
    # sequential/parallel/conditional/loop four-mode design. It was also the **only**
    # reader of `execution_config.max_iterations`; after deletion that field is wired by
    # `_resolve_loop_max_iterations` into the supervisor_loop engine (see that method).

    def _resolve_agent_identity(self, agent_config) -> Dict[str, Any]:
        """Resolve the agent_id inside sub_agents from an AgentConfigProxy and provide the owner info for persistence.

        A sub-agent's agent_config is an AgentConfigProxy (built by _load_agent_async)
        whose id is app_releases.id — it must not be written into agent_config_id
        (FK -> agent_configs.id), so release_id is used uniformly. agent_name feeds
        meta_data.agent_name and the detail page's node_name.
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
        """execution_config.parallel_limit (invalid values fall back to the schema default 3).

        The schema enforces `ge=1`, but legacy JSON / manual DB edits may yield 0,
        negative numbers, or an empty string. Such values fall back to 3 (matching the
        schema default) rather than 1 — 1 would silently degrade parallelism to serial
        execution and the slowdown would be misdiagnosed as "the model got slower".
        """
        cfg = self._execution_config or {}
        raw = cfg.get("parallel_limit", None)
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return 3
        return limit if limit >= 1 else 3

    async def _gather_limited(self, coros: List[Any]) -> List[Any]:
        """gather with a concurrency cap; semantics match `gather(..., return_exceptions=True)`.

        Replaces the former "batched barrier" approach: batching synchronizes within each
        batch — with limit=3 and 5 sub-agents, the second batch only starts after the
        whole first batch finishes, so total time = the sum of the slowest per batch. A
        semaphore instead frees a slot as soon as any task completes, giving total time =
        slowest single task + queueing overhead. Result order matches input order (same
        as gather).
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
        """Read the timeout/retry guardrails from execution_config.

        Returns:
            (total_timeout, stream_idle_timeout, retry_on_failure, max_retries)

        The two timeout semantics are **deliberately separate**:

        - `total_timeout` = `execution_config.timeout` (default 60s) — the non-streaming
          path's **total duration** cap. Non-streaming returns in one shot, so a timeout
          means failure; no ambiguity.
        - `stream_idle_timeout` = `execution_config.stream_idle_timeout` (default 300s) —
          the streaming path's **inter-event idle** cap.

        Why `timeout` is not reused as the streaming idle timeout: while a sub-agent runs
        tools (sandbox code execution, web search), the gap between `tool_start` and
        `tool_end` is **completely silent** and legitimately exceeds 60s. Using 60s as the
        idle threshold would kill valid tool calls — trading a "stuck" problem for a
        "false-killed feature" problem, which is harder to diagnose. 300s still catches
        real deadlocks (a permanently frozen spinner) without hurting long tools. <=0
        means no time limit.
        """
        cfg = self._execution_config or {}

        def _num(key: str, default: float = 0.0) -> float:
            """Fetch a numeric config: missing / None / empty string / dirty values all fall back to default.

            The empty string must fall back to default rather than 0: parsing
            `stream_idle_timeout=''` as 0 would **silently disable the deadlock
            guardrail** (0 means "no time limit" under this convention), and that is
            exactly the shape most likely to appear in legacy config (the frontend stores
            an empty string after a form field is cleared). An explicit numeric 0
            respects user intent = guardrail off.
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
        """Only **transient** failures are worth retrying.

        Three classes are not retried:
        1. `BusinessException` — missing config / validation failure; a rerun fails the
           same way and only burns quota;
        2. `asyncio.CancelledError` — cancellation is external intent; swallowing it
           would break graceful shutdown;
        3. Timeout (`TimeoutError`) — the LLM was too slow; a retry usually just waits
           for another timeout, and making a request that already waited 60s wait
           another 60s feels like a total freeze to the user.
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
        """Execute a single sub-agent (timeout + retry guardrail shell).

        The guardrail sits here rather than at the 14 call sites — wrapping them
        individually would inevitably miss some and the semantics would drift. Retrying
        on the non-streaming path is safe: the result is invisible to the caller until
        fully returned, so the frontend never sees duplicated content (the cost is a
        failed execution record for the failed attempt, which is desirable observability).
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
            except Exception as e:  # noqa: BLE001 - the guardrail needs to see all exceptions before deciding
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
        # Theoretically unreachable (the loop always returns or raises)
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
        """Execute a single sub-agent (streaming; idle timeout + retry within idempotent bounds).

        **Timeout semantics**: streaming uses the "inter-event idle timeout"
        (`stream_idle_timeout`, default 300s) instead of `timeout` (60s). While a
        sub-agent runs sandbox code / web search, the span between `tool_start` and
        `tool_end` is silent and legitimately exceeds 60s — using 60s as the idle
        threshold would falsely kill valid tool calls. 300s still catches real deadlocks
        (the user-visible symptom is a permanently frozen spinner).

        **Retry boundary**: retries happen only before **a single event has been
        emitted**. Once the frontend has received dispatch/content, a rerun duplicates
        bubble content and creates two running execution records; such a "half-way
        retry" is worse than none. The effective scope is therefore pre-flight failures
        (model config read failure, connection setup failure, etc.), which are also the
        cases most likely to succeed on retry.
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
                        # Idle timeout: the underlying generator is cancelled by wait_for
                        # and is not retried (it already ran that long; a rerun would
                        # just make the user wait again)
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
                # Both the retry and exception paths must close the generator, otherwise
                # the underlying run_stream async context (DB sessions etc.) is not
                # released promptly
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
        """Execute a single sub-agent (streaming) — the real implementation of one attempt.

        Args:
            agent_config: Agent config
            message: Message
            context: Context
            conversation_id: Conversation ID
            user_id: User ID

        Yields:
            SSE-formatted event stream
        """
        from .draft_run_service import AgentRunService

        async with self._sub_agent_db() as agent_db:
            # Load the model config
            model_config = await self._db_get(
                ModelConfig, agent_config.default_model_config_id, db=agent_db
            )
            if not model_config:
                raise BusinessException(
                    "Agent 模型配置不存在",
                    BizCode.AGENT_CONFIG_MISSING
                )
            # History convention, see _sub_agent_history: supervisor_loop sees
            # only the task brief (empty history); three-stage supervisor
            # sub-agents answer the user directly and carry the cluster history.
            history = await self._sub_agent_history(conversation_id, db=agent_db)

            # Stream the agent execution
            # Attribution comes from instance state (current_execution_id) + a local lookup
            # rather than adding parameters at all 10 call sites — that would inevitably miss
            # some (the streaming path once blew up on a foreign key because of this).
            # Sub-layer tokens are accounted uniformly here (the sub-agent's sub_usage event
            # is absorbed into the ledger at this point and no longer passed through, so the
            # outer layer no longer parses SSE strings to accumulate).
            draft_service = AgentRunService(agent_db)
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
                # The memory switch's single source of truth = cluster config (all
                # orchestration modes and call paths converge here); the memory parameter
                # exists only for signature compatibility and no longer drives decisions
                memory=self.cluster_memory_enabled(),
                sub_agent=True,
                history=history,
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
        """Execute a single sub-agent — the real implementation of one attempt.

        Args:
            agent_config: Agent config
            message: Message
            context: Context
            conversation_id: Conversation ID
            user_id: User ID


        Returns:
            Execution result
        """
        from .draft_run_service import AgentRunService

        async with self._sub_agent_db() as agent_db:
            # Load the model config
            model_config = await self._db_get(
                ModelConfig, agent_config.default_model_config_id, db=agent_db
            )
            if not model_config:
                raise BusinessException(
                    "Agent 模型配置不存在",
                    BizCode.AGENT_CONFIG_MISSING
                )
            # Same as the streaming path (see _sub_agent_history)
            history = await self._sub_agent_history(conversation_id, db=agent_db)

            # Execute the agent
            # Sub-layer tokens are accounted uniformly here (usage.total_tokens from the
            # non-streaming result goes into the ledger).
            draft_service = AgentRunService(agent_db)
            result = await draft_service.run(
                agent_config=agent_config,
                model_config=model_config,
                message=message,
                workspace_id=agent_config.app.workspace_id,
                conversation_id=str(conversation_id) if conversation_id else None,
                user_id=user_id,
                variables=context,
                web_search=web_search,
                # The memory switch's single source of truth = cluster config (same as the
                # streaming path)
                memory=self.cluster_memory_enabled(),
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                sub_agent=True,
                history=history,
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
        """Merge sub-agent results

        Args:
            results: Sub-agent execution results

        Returns:
            Merged result
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
        """Merge all results

        Args:
            results: Execution results

        Returns:
            Merged result
        """
        if isinstance(results, list):
            # Results from sequential or parallel execution
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
            # Results from conditional or loop execution
            if "result" in results:
                return results["result"].get("message", "")
            return str(results)

        return str(results)

    def _vote_results(self, results: Any) -> str:
        """Pick the best result by vote (simplified version)

        Args:
            results: Execution results

        Returns:
            Best result
        """
        # Simplified: return the first successful result
        if isinstance(results, list):
            for item in results:
                if "result" in item:
                    return item["result"].get("message", "")

        return self._merge_results(results)

    def _priority_results(self, results: Any) -> str:
        """Pick a result by priority (simplified version)

        Args:
            results: Execution results

        Returns:
            Highest-priority result
        """
        # Simplified: return the first result
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
        """Collaboration mode streaming execution - agents can hand off to each other

        Uses handoffs_service to implement dynamic switching between agents

        Args:
            message: User message (already rendered per the variable contract at entry)
            conversation_id: Conversation ID
            user_id: User ID
            web_search: Whether web search is enabled
            memory: Whether memory is enabled
            storage_type: Storage type
            user_rag_memory_id: RAG memory ID
            variables: Cluster variable value bag. These parameters used to be dropped
                entirely when calling handoffs, so collaboration mode had no variable
                rendering and no call context at all. They now all pass through:
                variables feed node rendering, the rest go into the LangChain call
                metadata (observability attribution).

        Yields:
            SSE-formatted event stream
        """
        from .handoffs_service import (
            convert_multi_agent_config_to_handoffs,
            HandoffsService
        )

        try:
            # 1. Build the multi_agent_config dict
            multi_agent_config = {
                "sub_agents": self._effective_sub_agent_entries or self.config.sub_agents,
                "orchestration_mode": self.config.orchestration_mode
            }

            # 2. Convert the config (each agent keeps its own model_view)
            agent_configs = await convert_multi_agent_config_to_handoffs(
                multi_agent_config,
                self.db
            )

            if not agent_configs:
                raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

            # 3. Create the HandoffsService
            # The variable bag + call context (user_id/memory/storage_type/
            # user_rag_memory_id) are injected once here, so each agent's system_prompt
            # is fully rendered when nodes are built. The checkpoint is externalized to
            # Redis (falling back to an in-process MemorySaver when probing fails), and
            # thread_prefix carries the tenant ID for isolation. The fallback decision
            # lives here in one place; HandoffsService just uses whatever it is given.
            from ..agent.redis_checkpoint import create_async_checkpointer
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

            # 4. Use handoffs_service's streaming chat
            conv_id = str(conversation_id) if conversation_id else None

            # Collaboration mode bypasses AgentRunService, so sub-agent records are
            # backfilled per "node activation": an agent event opens a sub record, and
            # the next agent / end / error closes the previous one. The same agent
            # activated multiple times yields multiple rows, each with a distinct
            # execution_id.
            active_execution_id = None
            active_agent_key = None
            active_started_at = 0.0
            active_tokens = 0
            # Collaboration mode has no AgentTraceRecorder trace, so the "call-chain
            # detail" is accumulated manually per activation: the task the agent
            # received / the text it streamed / the handoffs it initiated.
            active_task = message
            active_text = ""
            active_steps: list = []
            pending_task = None

            def _activation_steps(status: str, agent_name: Optional[str]) -> list:
                """Fold one activation into steps: the first item is its own input/output, followed by the handoffs it initiated."""
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

                # Agent switch: close the previous activation first, then open the new one
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
                    # Task handed over via handoff: prefer the unhandled_question
                    # written by the previous activation
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
                    # Tokens go into the per-turn ledger (sub layer); the
                    # activation-local active_tokens are used only for this activation's
                    # agent_complete / execution record; sub_usage is no longer passed
                    # outward (the outer layer no longer accumulates; the ledger is
                    # published in the end event).
                    _evt_tokens = int(event_data.get("total_tokens") or 0)
                    active_tokens += _evt_tokens
                    self._turn_sub_tokens += _evt_tokens
                    continue
                elif event_name == "message":
                    active_text += event_data.get("content") or ""
                elif event_name == "handoff":
                    # The handoff belongs to the initiator's activation: whom it handed
                    # to, why, and what remains unanswered
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

                # Attribution injection: only fill fields missing from data; the event
                # name and other fields stay as-is
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
                    # The whole turn ended with no activation record (degraded case):
                    # inject only the orchestration mode so the frontend can tell
                    event = self._inject_agent_meta(event, {
                        "orchestration_mode": self._normalized_mode,
                    })

                # handoffs' own start / end belong to the "collaboration run lifecycle",
                # not the cluster conversation (same convention as the supervisor branch,
                # see _SUB_RUN_LIFECYCLE_EVENTS), so neither is passed through:
                # - end: the cluster-level end is emitted uniformly by execute_stream
                #   after the mode branch; passing it through would surface two ends
                #   (and the second end has already finalized the outer message);
                # - start: with the conversation_id stripped it becomes an empty start,
                #   which the share frontend's start branch would use to overwrite the
                #   captured conversation ID with undefined, so the turn never backfills
                #   it; the next turn then carries a null conversation ID and the
                #   backend creates a new conversation every turn (dialogue split).
                # All other events first have the sub-run conversation_id stripped
                # (prevents the frontend's conversation pointer from being poisoned by
                # handoffs' internal conversation ID — the cluster conversation ID is
                # only ever published by start/end).
                if event_name not in _SUB_RUN_LIFECYCLE_EVENTS:
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
            # Close any sub-agent record still "activating" so its status never stays
            # stuck in running (a NameError from an exception raised before the
            # activation variables were defined is swallowed too)
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
        """Collaboration mode non-streaming execution - agents can hand off to each other

        Uses handoffs_service to implement dynamic switching between agents

        Args:
            message: User message (already rendered per the variable contract at entry)
            conversation_id: Conversation ID
            user_id: User ID
            variables: Cluster variable value bag (previously accepted then dropped, so
                collaboration mode had no variable rendering)
            storage_type: Storage type (goes into the call context)
            user_rag_memory_id: User RAG memory ID (same as above)

        Returns:
            Execution result
        """
        from .handoffs_service import (
            convert_multi_agent_config_to_handoffs,
            HandoffsService
        )

        start_time = time.time()

        try:
            # 1. Build the multi_agent_config dict
            multi_agent_config = {
                "sub_agents": self._effective_sub_agent_entries or self.config.sub_agents,
                "orchestration_mode": self.config.orchestration_mode
            }

            # 2. Convert the config (each agent keeps its own model_view)
            agent_configs = await convert_multi_agent_config_to_handoffs(
                multi_agent_config,
                self.db
            )

            if not agent_configs:
                raise BusinessException("没有可用的子 Agent", BizCode.AGENT_CONFIG_MISSING)

            # 3. Create the HandoffsService
            # Same conventions as the streaming path — the variable bag feeds node
            # rendering, the call context goes into the LLM metadata, and the checkpoint
            # uses Redis with a tenant-prefixed thread_id.
            from ..agent.redis_checkpoint import create_async_checkpointer
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

            # 4. Use handoffs_service's non-streaming chat
            conv_id = str(conversation_id) if conversation_id else None

            result = await handoffs_service.chat(
                message=message,
                conversation_id=conv_id
            )

            elapsed_time = time.time() - start_time

            # Sub-execution records (aligned with the streaming path's tree structure):
            # non-streaming has no per-activation real-time events, so it degrades to
            # "backfill from the activation history after the run" — handoff_history is
            # the sequence of agents activated in this run (the first agent is
            # create_route_initial's default/active, then each agent that initiated a
            # handoff — the target agent is picked up by the following activation, in
            # real execution order). Every record: parent=master, conversation_id=cluster
            # conversation, status=completed. Observability degradation never blocks the
            # conversation (same style as _ensure_master_execution).
            try:
                # With zero handoffs, handoff_history is empty and active_agent may be
                # None; fall back to the default agent (create_route_initial's first
                # choice = the first key of agent_configs)
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
                # Conversation attribution: the non-streaming entry
                # (multi_agent_service.run) persists with request.conversation_id and
                # does not consume this field; here we return the cluster conversation
                # as well to prevent callers from mistaking it for a sub-run ID
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
        """Format an SSE event

        Args:
            event: Event type
            data: Event data

        Returns:
            SSE-formatted string
        """
        import json
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    @staticmethod
    def _strip_conversation_id(event: str) -> str:
        """Strip the conversation_id field from sub-agent events.

        A sub-agent run (AgentRunService.run_stream with sub_agent=True / handoffs)
        carries the **sub-run conversation's** conversation_id in its message / end etc.
        events. If the upstream layer passed it through verbatim, the frontend's
        currentConversationId / syncConversationId would be displaced by the sub-agent
        conversation — the next request would hit a different conversation and the same
        turn would be split into another conversation in the DB. The single authoritative
        source of the conversation ID is the orchestrator's start/end events (cluster
        conversation ID). All other fields (content/agent/usage/…) stay as-is.
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
        """Record the merge mode actually executed for this turn's result aggregation.

        execution_config.result_merge_mode declares the desired mode; actual execution
        may degrade to plain concatenation due to a single result skipping the merge, a
        missing Master model/key, an aggregation exception, etc. The value is written to
        the end event's merge_mode_actual field so the frontend/logs can see silent
        degradations such as "configured master but actually smart concatenation".
        reason goes to logs only, never the event.
        """
        self._merge_mode_actual = mode
        if mode != "master" or reason:
            logger.info(
                "结果整合实际模式",
                extra={"merge_mode_actual": mode, "reason": reason},
            )

    async def _load_agent_async(self, release_id: uuid.UUID):
        """Load agent config from an app release

        Args:
            release_id: Release ID

        Returns:
            Agent config object (containing the release's config data)
        """
        from ..models import AppRelease, App

        # Load the release
        release = await self._db_get(AppRelease, release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(release_id))

        # Get the agent config from the release's config
        config_data = release.config
        if not config_data:
            raise BusinessException(f"发布版本 {release_id} 缺少配置数据", BizCode.AGENT_CONFIG_MISSING)

        # Load the app info (for workspace_id)
        app = await self._db_get(App, release.app_id)
        if not app:
            raise ResourceNotFoundException("应用", str(release.app_id))

        # Create an AgentConfig-like object carrying all needed attributes
        class AgentConfigProxy:
            """Agent config proxy object emulating the AgentConfig interface"""
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
        """Execute multi-agent collaboration

        Args:
            message: User message
            collaboration_agents: List of collaborating agents
            strategy: Collaboration strategy (sequential/parallel/hierarchical)
            initial_context: Initial context
            conversation_id: Conversation ID
            user_id: User ID
            routing_decision: Routing decision

        Returns:
            Collaboration execution result
        """
        logger.info(
            "开始多 Agent 协作",
            extra={
                "agent_count": len(collaboration_agents),
                "strategy": strategy
            }
        )

        if strategy == "decomposition":
            # Question decomposition: each agent handles one sub-question
            return await self._execute_decomposition_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        elif strategy == "sequential":
            # Sequential collaboration: run in order; later agents can use earlier results
            return await self._execute_sequential_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        elif strategy == "parallel":
            # Parallel collaboration: run all agents concurrently
            return await self._execute_parallel_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        elif strategy == "hierarchical":
            # Hierarchical collaboration: a primary agent coordinates, others assist
            return await self._execute_hierarchical_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )
        else:
            # Default to sequential collaboration
            return await self._execute_sequential_collaboration(
                message, collaboration_agents, initial_context,
                conversation_id, user_id, routing_decision
            )

    def _check_dependencies(self, sub_questions: List[Dict[str, Any]]) -> bool:
        """Detect whether the sub-questions have dependencies

        Args:
            sub_questions: Sub-question list

        Returns:
            True if there are dependencies, False if fully independent
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
        """Question-decomposition execution

        Each agent handles one independent sub-question, avoiding duplication.

        Example:
        Original question: "write an old-style poem about snow, and compute 3+8"
        After decomposition:
        - Sub-question 1: "write an old-style poem about snow" -> humanities tutor
        - Sub-question 2: "compute 3+8" -> science tutor

        Args:
            collaboration_agents: In decomposition mode, this is the sub_questions list
        """
        results = []

        # In decomposition mode, collaboration_agents IS the sub_questions list
        sub_questions = collaboration_agents

        if not sub_questions:
            # No sub-questions: fall back to plain collaboration
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

        # Detect dependencies
        has_dependencies = self._check_dependencies(sub_questions)

        # Load the execution-mode config
        execution_mode = self._execution_config.get("sub_agent_execution_mode", "parallel")

        # Dependencies present: force serial mode
        if has_dependencies:
            logger.info("检测到子问题有依赖关系，强制使用串行执行")
            execution_mode = "sequential"

        if execution_mode == "sequential":
            # Serial execution mode
            logger.info(f"串行执行 {len(sub_questions)} 个子问题")

            # Stores completed sub-question results (indexed by order)
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

                # With dependencies, build a context that includes the dependency results
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

                # Serial execution
                try:
                    result = await self._execute_sub_agent(
                        agent_data["config"],
                        sub_question,
                        context_with_deps,  # context including dependency results
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
                    completed_results[order] = result_entry  # save the result for later dependents
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
            # Parallel execution mode (default)
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

                # Create the async task
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

            # Run all tasks in parallel
            logger.info(f"并行执行 {len(tasks)} 个子问题")
            task_results = await self._gather_limited(tasks)

            # Process the results
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

        # Merge the results (question-decomposition mode)
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
        """Sequential collaboration execution

        Each agent runs in order; later agents can see earlier agents' results.
        """
        results = []
        context = initial_context.copy()
        accumulated_response = []

        # Sort by order
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

            # Build this agent's message (task instructions + earlier results)
            agent_message = message
            if context.get("previous_results"):
                agent_message = f"""原始问题：{message}

你的任务：{agent_task}

前面专家的分析结果：
{context['previous_results']}

请基于以上信息，完成你的任务。"""

            # Execute the agent
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

            # Update the context
            context[f"result_from_{agent_name}"] = agent_response

            # Accumulate the response
            accumulated_response.append(f"【{agent_name}】\n{agent_response}")

            # Update previous_results for the next agent
            context["previous_results"] = "\n\n".join(accumulated_response)

        # Merge the final results
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
        """Parallel collaboration execution

        All agents run concurrently and independently.
        """
        tasks = []
        agent_infos = []

        for agent_info in collaboration_agents:
            agent_id = agent_info["agent_id"]
            agent_data = self.sub_agents.get(agent_id)

            if not agent_data:
                continue

            agent_task = agent_info.get("task", "处理任务")

            # Build this agent's message
            agent_message = f"""原始问题：{message}

你的任务：{agent_task}

请完成你的任务。"""

            # Create the task
            task = self._execute_sub_agent(
                agent_data["config"],
                agent_message,
                initial_context.copy(),
                conversation_id,
                user_id
            )
            tasks.append(task)
            agent_infos.append((agent_id, agent_data, agent_info))

        # Run in parallel
        task_results = await self._gather_limited(tasks)

        # Process the results
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

        # Merge the results
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
        """Hierarchical collaboration execution

        The primary agent coordinates; the other agents provide supporting input.
        """
        # Find the primary agent and the secondary agents
        primary_agent = None
        secondary_agents = []

        for agent_info in collaboration_agents:
            if agent_info.get("role") == "primary":
                primary_agent = agent_info
            else:
                secondary_agents.append(agent_info)

        if not primary_agent:
            # No primary agent specified: use the first one
            primary_agent = collaboration_agents[0]
            secondary_agents = collaboration_agents[1:]

        # 1. Run the secondary agents first (in parallel)
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

            # Run the secondary agents in parallel
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

        # 2. Run the primary agent (merging the secondary agents' results)
        primary_agent_id = primary_agent["agent_id"]
        primary_agent_data = self.sub_agents.get(primary_agent_id)

        if not primary_agent_data:
            raise BusinessException(f"主协作 Agent 不存在: {primary_agent_id}", BizCode.AGENT_CONFIG_MISSING)

        # Build the primary agent's message (including the secondary agents' results)
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

        # Run the primary agent
        primary_result = await self._execute_sub_agent(
            primary_agent_data["config"],
            primary_message,
            initial_context,
            conversation_id,
            user_id
        )

        primary_agent_name = primary_agent_data.get("info", {}).get("name", primary_agent_id)

        # Merge all results
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
        """Merge the question-decomposition results

        Each agent handled a different sub-question; the answers are combined in order.

        Args:
            results: Result list; each entry contains sub_question and result
            original_question: Original user question

        Returns:
            Merged response
        """
        if not results:
            return "未获取到有效结果"

        # Load the merge mode
        # The default must be master (aligned with the schema declaration and the
        # streaming path at :1327): with the old config missing the key, a smart default
        # would make non-streaming sessions always use plain concatenation, diverging
        # from streaming behavior (the same app would give different-quality final
        # answers depending on the call style).
        merge_mode = self._execution_config.get("result_merge_mode", "master")

        if merge_mode == "master":
            # Merge via the Master Agent
            return await self._master_merge_results(results, "decomposition", original_question)
        else:
            # smart mode: combine the answers directly
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
        """Merge collaboration results (smart dedup and combination)

        Args:
            results: Collaboration result list
            strategy: Collaboration strategy
            original_question: Original user question

        Returns:
            Merged response
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

        # Load the merge strategy config
        # The default must be master (aligned with the schema declaration and the
        # streaming path at :1327): with the old config missing the key, a smart default
        # would make non-streaming sessions always use plain concatenation, diverging
        # from streaming behavior (the same app would give different-quality final
        # answers depending on the call style).
        merge_mode = self._execution_config.get("result_merge_mode", "master")

        if merge_mode == "master":
            # Master Agent merge: let the Master Agent combine the original question and
            # sub-agent answers into the final reply
            return await self._master_merge_results(results, strategy, original_question)
        else:
            # Default to smart merging
            return self._smart_merge_results(results, strategy)

    # Chunk size (characters) for smart merging. smart does not call a model and has no
    # natural token rhythm; pushing by chunks lets the main bubble grow progressively
    # instead of the whole text appearing at once.
    _SMART_MERGE_CHUNK_CHARS = 240

    @staticmethod
    def _chunk_text(text: str, max_chars: int):
        """Split long text into chunks: paragraph-first, hard length cuts for oversized paragraphs.

        Used for the smart (concatenation) path's streaming output. Empty text produces
        no chunks.
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
        """Streaming version of smart concatenated results (no model call).

        Used as the fallback path: when Master merging is unavailable (no model
        configured / no usable API key / call exception) or
        `result_merge_mode="smart"`, the concatenated result is yielded chunk by chunk
        so the main bubble never "appears all at once".

        Yields:
            SSE-formatted message events
        """
        final_response = self._smart_merge_results(results, strategy)
        if not final_response:
            logger.warning("smart 整合结果为空，不输出 message 事件")
            return
        for chunk in self._chunk_text(final_response, self._SMART_MERGE_CHUNK_CHARS):
            yield self._format_sse_event("message", {"content": chunk})
            # Yield the event loop: ensure each chunk flushes independently so the
            # frontend renders chunk by chunk
            await asyncio.sleep(0)

    def _smart_merge_results(
        self,
        results: List[Dict[str, Any]],
        strategy: str
    ) -> str:
        """Smart merge results (dedup, key-information extraction)

        Use case: multiple agents answering similar questions, needing dedup and cleanup.

        Note: in streaming scenarios the user has already seen every agent's output, so
        this mainly produces a "merged version" for later use (e.g. saving to the DB).
        """
        if not results:
            return ""

        # Collect all messages
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
            # Single result: return it directly
            return messages[0]

        # Multiple results: smart-merge according to the strategy
        if strategy == "decomposition":
            # Question decomposition: combine all sub-question answers
            # Combine the agents' answers in order
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
            # Sequential collaboration: return the last agent's result (it already
            # includes the earlier information)
            return self._merge_sequential_smart(results)

        elif strategy == "parallel":
            # Parallel collaboration: check whether dedup is needed
            return self._merge_parallel_smart(results)

        elif strategy == "hierarchical":
            # Hierarchical collaboration: return only the primary agent's result
            return self._merge_hierarchical_smart(results)

        else:
            # Default: return the most complete one
            return max(messages, key=len)

    def _merge_sequential_smart(self, results: List[Dict[str, Any]]) -> str:
        """Smart-merge sequential collaboration results

        Sequential trait: later agents reference the earlier results.
        Strategy: keep only the last agent's full answer (it already includes the
        earlier information).
        """
        if not results:
            return ""

        # Get the last successful result
        for result in reversed(results):
            if "error" not in result:
                message = result.get("result", {}).get("message", "")
                if message:
                    return message

        return "未获取到有效结果"

    def _merge_parallel_smart(self, results: List[Dict[str, Any]]) -> str:
        """Smart-merge parallel collaboration results

        Parallel trait: multiple independent viewpoints.
        Strategy:
        1. If the answers are highly similar (duplicates), keep only one
        2. If they differ, combine all viewpoints (without showing agent names)
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

        # Check similarity
        similarity = self._calculate_similarity(messages)

        if similarity > 0.7:
            # Highly similar: return only the longest one
            return max(messages, key=len)
        else:
            # Different viewpoints: combine them (without showing agent names)
            # Use separators to delimit the parts
            return "\n\n---\n\n".join(messages)

    def _merge_hierarchical_smart(self, results: List[Dict[str, Any]]) -> str:
        """Smart-merge hierarchical collaboration results

        Hierarchical trait: the primary agent has already synthesized the secondary
        agents' input.
        Strategy: return only the primary agent's result.
        """
        # Find the primary agent's result
        for result in results:
            if result.get("role") == "primary":
                message = result.get("result", {}).get("message", "")
                if message:
                    return message

        # No primary agent found: return the last one
        if results:
            last_result = results[-1]
            return last_result.get("result", {}).get("message", "")

        return "未获取到有效结果"

    def _resolve_merge_params(self) -> Dict[str, Any]:
        """Generation parameters for the Master merge stage.

        - max_tokens: read from `execution_config.merge_max_tokens` (default 8192).
          It used to be hardcoded to 2000, which truncated long reports' merged output —
          and the merged output is exactly the final answer the user sees.
        - temperature: follows the multi-agent config's model_parameters; 0.7 when unset.
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
        """Merge multiple sub-agent results using the Master Agent

        Args:
            results: Sub-agent response result list
            strategy: Collaboration strategy
            original_question: Original user question

        Returns:
            Final reply merged by the Master Agent
        """
        if not results:
            return "没有收到任何 Agent 的响应"

        if len(results) == 1:
            # Single result: return it directly
            return results[0].get('result', {}).get('message', '')

        # Build the summary of sub-agent answers
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

        # Build the Master Agent merge prompt
        responses_text = ""
        for resp in agent_responses:
            agent_name = resp['agent_name']
            task = resp['task']
            response = resp['response']

            if task:
                responses_text += f"\n### {agent_name}（任务：{task}）的回答：\n{response}\n"
            else:
                responses_text += f"\n### {agent_name} 的回答：\n{response}\n"

        # Adjust the merge instruction based on the strategy
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
            # Call the Master Agent's LLM to merge
            from ..model_clients import RedBearChatModel

            # Load the Master Agent's model config
            default_model_config_id = self.config.default_model_config_id
            if not default_model_config_id:
                logger.warning("没有配置 Master Agent，使用简单整合")
                return self._smart_merge_results(results, strategy)

            # Load the model view (non-decrypted view; calls go through the model-service
            # invoke seam)
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

            # Create the LLM instance
            _merge_params = self._resolve_merge_params()
            llm = RedBearChatModel.for_invoke(
                model_view,
                params={
                    "temperature": _merge_params["temperature"],
                    "max_tokens": _merge_params["max_tokens"],
                },
            )

            # Call the model to merge
            response = await llm.ainvoke(merge_prompt)

            # Extract the tokens consumed by the merge
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

            # Extract the response content
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
            # Fall back to smart merging
            return self._smart_merge_results(results, strategy)

    async def _master_merge_results_stream(
        self,
        results: List[Dict[str, Any]],
        strategy: str,
        original_question: str = None
    ):
        """Stream-merge multiple sub-agent results using the Master Agent

        Args:
            results: Sub-agent response result list
            strategy: Collaboration strategy
            original_question: Original user question

        Yields:
            SSE-formatted event stream
        """
        if not results:
            yield self._format_sse_event("message", {"content": "没有收到任何 Agent 的响应"})
            return

        if len(results) == 1:
            # Single result: return it directly
            yield self._format_sse_event("message", {
                "content": results[0].get('result', {}).get('message', '')
            })
            return

        # Build the summary of sub-agent answers (same as the non-streaming version)
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

        # Build the merge prompt
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
            from ..model_clients import RedBearChatModel

            # Load the Master Agent's model config
            default_model_config_id = self.config.default_model_config_id
            if not default_model_config_id:
                logger.warning("没有配置 Master Agent，降级为 smart 拼接（分块流式）")
                async for _evt in self._smart_merge_results_stream(results, strategy):
                    yield _evt
                return

            # Load the model view (non-decrypted view; calls go through the model-service
            # invoke seam)
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

            # Create the LLM instance (streaming)
            # max_tokens goes through execution_config.merge_max_tokens (default 8192):
            # hardcoding 2000 would truncate long reports' merged output — and the merged
            # output is exactly the final answer the user sees.
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

            # Stream the model call for merging
            try:
                chunk_count = 0
                logger.debug(f"开始流式调用，model={model_view.model_name}")

                async for chunk in llm.astream(merge_prompt):
                    chunk_count += 1

                    # LangChain streaming puts the cumulative usage on the (usually
                    # last) chunk's usage_metadata — it is a "cumulative value", not an
                    # increment, so overwrite with the latest value instead of
                    # accumulating (some providers report cumulative values per chunk;
                    # summing would double-count).
                    _um = getattr(chunk, 'usage_metadata', None)
                    if isinstance(_um, dict) and _um.get("total_tokens"):
                        self._turn_merge_tokens = int(_um.get("total_tokens") or 0)

                    # Extract the content
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
                # The underlying model does not support astream: fall back to a one-shot
                # call, but still push the result by chunks so the main bubble never
                # "appears all at once"
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
            # Fall back to smart concatenation, also streamed by chunks
            async for _evt in self._smart_merge_results_stream(results, strategy):
                yield _evt

    def _should_merge_results(
        self,
        results: List[Dict[str, Any]],
        strategy: str
    ) -> bool:
        """Decide whether the results need merging

        Args:
            results: Agent execution results
            strategy: Collaboration strategy

        Returns:
            True if merging is needed, False otherwise
        """
        if not results or len(results) == 1:
            # No results or a single result: no merge needed
            return False

        if strategy == "decomposition":
            # Question decomposition: sub-questions are independent and the user has
            # already seen every answer; usually no merge is needed (unless configured)
            return self._execution_config.get("force_merge_decomposition", True)

        if strategy == "hierarchical":
            # Hierarchical collaboration: the primary agent already merged the rest
            return False

        # sequential and parallel modes: a merge/dedup may be needed
        return True

    async def _parallel_stream_agents(
        self,
        agent_tasks: List[Tuple[str, str, Any, str, Dict[str, Any]]],
        conversation_id: Optional[uuid.UUID],
        user_id: Optional[str]
    ) -> AsyncIterator[Tuple[str, str, str, str]]:
        """Run multiple agents in parallel streams, returning results in real time

        Args:
            agent_tasks: [(agent_id, agent_name, agent_config, message, context), ...]
            conversation_id: Conversation ID
            user_id: User ID

        Yields:
            (agent_id, agent_name, event_type, content) tuples
        """
        # Create an async generator per agent
        async def stream_single_agent(agent_id, agent_name, agent_config, message, context):
            """Streaming execution wrapper for a single agent"""
            try:
                async for event in self._execute_sub_agent_stream(
                    agent_config,
                    message,
                    context,
                    conversation_id,
                    user_id
                ):
                    # Parse the event
                    if "data:" in event:
                        try:
                            import json
                            data_line = event.split("data: ", 1)[1].strip()
                            data = json.loads(data_line)

                            if "content" in data:
                                yield (agent_id, agent_name, "content", data["content"])
                            elif _sse_event_name(event) in _CLUSTER_OBSERVABILITY_EVENTS:
                                # Parallel execution interleaves events; attach agent
                                # attribution before handing off to the upstream router
                                yield (
                                    agent_id,
                                    agent_name,
                                    "raw",
                                    self._inject_agent_meta(event, self._sub_event_meta(agent_id, agent_name)),
                                )
                        except:
                            pass

                # Send the completion signal
                yield (agent_id, agent_name, "done", "")

            except Exception as e:
                logger.error(f"Agent {agent_name} 流式执行失败: {str(e)}")
                yield (agent_id, agent_name, "error", str(e))

        # Create the streaming tasks for all agents
        streams = []
        for agent_id, agent_name, agent_config, message, context in agent_tasks:
            stream = stream_single_agent(agent_id, agent_name, agent_config, message, context)
            streams.append(stream)

        # Use a queue to merge multiple async streams
        queue = asyncio.Queue()
        active_streams = len(streams)

        # Streaming concurrency throttling. This used to be a bare create_task with full
        # concurrency — `parallel_limit` only applied to the non-streaming
        # `_gather_limited`, while streaming is the main path (frontend debug preview and
        # production chat both go through here), so the guardrail was effectively absent
        # on the path users see: N sub-agents hit the model at once with only per-agent
        # idle timeouts as backstop.
        #
        # The semaphore must **span the whole stream's lifetime** (held until that
        # sub-agent finishes), not a single yield — releasing as soon as the generator
        # yields would immediately restore full concurrency, defeating the limit.
        sem = asyncio.Semaphore(self._parallel_limit())

        async def consume_stream(stream, stream_id):
            """Consume one stream into the queue (holding the semaphore until the stream ends)"""
            nonlocal active_streams
            try:
                async with sem:
                    async for item in stream:
                        await queue.put(item)
            finally:
                active_streams -= 1
                if active_streams == 0:
                    await queue.put(None)  # all streams finished

        # Start the consumer tasks for all streams
        tasks = [
            asyncio.create_task(consume_stream(stream, i))
            for i, stream in enumerate(streams)
        ]

        # Read from the queue and yield
        while True:
            item = await queue.get()
            if item is None:  # all streams finished
                break
            yield item

        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)

    def _calculate_similarity(self, messages: List[str]) -> float:
        """Compute message similarity (simplified version)

        Args:
            messages: Message list

        Returns:
            Similarity (0-1)
        """
        if len(messages) < 2:
            return 0.0

        # Simplified: compare length and keywords
        # Production use could employ more sophisticated algorithms (edit distance,
        # cosine similarity, etc.)

        # Compute the average length
        avg_length = sum(len(m) for m in messages) / len(messages)

        # Large length variance: treat as dissimilar
        length_variance = sum(abs(len(m) - avg_length) for m in messages) / len(messages)
        if length_variance > avg_length * 0.5:
            return 0.3

        # Extract keywords (simplified: first 50 chars)
        keywords = [m[:50] for m in messages]

        # Compute the repetition ratio
        unique_keywords = len(set(keywords))
        total_keywords = len(keywords)

        similarity = 1.0 - (unique_keywords / total_keywords)

        return similarity
