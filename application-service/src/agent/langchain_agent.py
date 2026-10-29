"""
LangChain Agent wrapper

Uses the standard LangChain 1.x approach:
- create the agent graph via create_agent
- support the tool-calling loop
- support streaming output
- support multiple providers through the remote model service (RedBearChatModel.for_invoke)
"""

import logging

import asyncio
import functools
import inspect
import json
import re
import time
import uuid as _uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional, Sequence

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError

from ..config import current_settings
from ..memory.retrieval_trace.stage_events import is_memory_stage_capture_enabled
from ..model_clients import RedBearChatModel
from ..utils.datetime_utils import to_iso_z, utcnow
from .prompt_blocks import build_execution_requirements

if TYPE_CHECKING:
    from ..schemas.model_schema import ModelInfo

logger = logging.getLogger(__name__)


def default_tool_call_limit() -> int:
    """Max calls per tool within one Agent run (prevents an endless loop while
    still leaving room for multi-round tasks). Single knob for every Agent kind,
    configured by the AGENT_TOOL_CALL_LIMIT environment variable (default 5)."""

    return current_settings().agent_tool_call_limit


# Upper bound for the optional evidence-assembly step that runs after a tool returns.
EVIDENCE_ASSEMBLY_TIMEOUT_S = 60.0

# A run that called tools but ends with less text than this (or with a sentence ending
# in a colon / ellipsis) did not answer the question — it stopped mid-narration. Such a
# turn gets one extra tool-free closing turn; see LangChainAgent._needs_closing_answer.
# Chosen well below a normal report and above a one-line "let me search" preamble.
CLOSING_ANSWER_MIN_CHARS = 200

CLOSING_ANSWER_INSTRUCTION = (
    "\n\n【本轮收尾】你已经完成工具调用。本轮不要再调用任何工具，"
    "必须直接输出面向用户的最终答案：把已获得的信息整理成结论，"
    "并逐项对照用户的问题；信息缺失或工具失败的部分如实说明缺什么，不得编造。"
)

# Extra graph steps added on top of the deterministic per-tool budget so a research
# run using all its tool calls still has room for intermediate reasoning and the final
# answer instead of dying one super-step before completion.
RECURSION_LIMIT_SAFETY_SLACK = 12

# Evidence budget when a run hits the graph step budget (the LangGraph message list is
# gone at that point, so usable tool results are rebuilt from node_executions):
# per-call body and the whole digest respectively.
CLOSING_ANSWER_EVIDENCE_PER_ITEM = 1200
CLOSING_ANSWER_EVIDENCE_TOTAL = 6000

# Used when the graph step budget is exhausted but usable tool results were already
# collected. The user gets a report built from that evidence; the turn is still
# reported as failed (the run did not finish its planned flow), but it is a failed
# turn that carries an answer the supervisor can adopt.
ABORTED_CLOSING_ANSWER_INSTRUCTION = (
    "\n\n【本轮收尾】处理步骤已达上限，本轮被中止，不会再有任何新信息。"
    "请只依据下面【已成功获取的工具结果】直接输出面向用户的最终答案："
    "先给出结论与关键数据，再逐项对照用户的问题；"
    "对未能获取的部分用一句话如实说明缺什么、为什么缺，不得编造，"
    "也不要再输出“我将去搜索”“让我继续”这类过程性描述。"
)

# Emitted by _over_limit_reply. Such an output is a rejection notice, not evidence.
_TOOL_REJECTION_MARKER = "[工具调用限制]"


# Default role used when the user configured no system prompt. It declares no tool
# capability; the 【执行要求】 section is rendered by ``prompt_blocks`` from the tools
# actually assigned (see prompt_blocks/__init__.py for how to add rules).
DEFAULT_AGENT_ROLE_PROMPT = "你是一个专业的 AI 助手，帮助用户可靠地完成任务。"


def build_agent_methodology(
    tool_names: Sequence[str],
    executed_tool_names: Sequence[str] = (),
    tool_call_limit: Optional[int] = None,
) -> str:
    """Render 【执行要求】 for the tools actually assigned to this Agent.

    Thin wrapper over ``prompt_blocks.build_execution_requirements``; this module holds
    no tool-specific prompt text.

    tool_names: tools the Agent can invoke live in this run.
    executed_tool_names: tools already executed upstream on the Agent's behalf (by the
        ToolOrchestrator for weak models); their results are already in the system prompt.
    tool_call_limit: per-tool call budget for one run, told to the model when set.
    """
    return build_execution_requirements(tool_names, executed_tool_names, tool_call_limit)


class AgentTraceRecorder:
    """Builds a Dify-style agent execution log grouped by LLM round."""

    def __init__(self, *, model: str, strategy: str, system_prompt: str, messages: Sequence[BaseMessage]):
        self._started_at = time.time()
        self._model = model
        self._current_iteration: dict[str, Any] | None = None
        self._current_llm: dict[str, Any] | None = None
        self._tools_by_id: dict[str, dict[str, Any]] = {}
        self.log: dict[str, Any] = {
            "meta": {
                "status": "running",
                "model": model,
                "strategy": strategy,
                "start_time": self._now_iso(),
                "elapsed_time": 0,
                "total_tokens": 0,
                "iterations": 0,
            },
            "iterations": [],
        }
        self._base_input = {
            "system_prompt": system_prompt,
            "messages": [self._message_to_dict(msg) for msg in messages],
        }

    @staticmethod
    def _now_iso() -> str:
        return to_iso_z(utcnow()) or ""

    @classmethod
    def _jsonable(cls, value: Any, max_length: int = 2000) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:max_length] + "...[truncated]" if len(value) > max_length else value
        if isinstance(value, BaseMessage):
            return cls._message_to_dict(value)
        if isinstance(value, dict):
            return {str(k): cls._jsonable(v, max_length) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._jsonable(item, max_length) for item in value]
        content = getattr(value, "content", None)
        if content is not None:
            return cls._jsonable(content, max_length)
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
        return text[:max_length] + "...[truncated]" if len(text) > max_length else text

    @classmethod
    def _message_to_dict(cls, message: BaseMessage) -> dict[str, Any]:
        return {
            "role": getattr(message, "type", message.__class__.__name__.replace("Message", "").lower()),
            "content": cls._jsonable(getattr(message, "content", "")),
        }

    @staticmethod
    def _strip_private(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: AgentTraceRecorder._strip_private(v) for k, v in value.items() if not str(k).startswith("_")}
        if isinstance(value, list):
            return [AgentTraceRecorder._strip_private(item) for item in value]
        return value

    def start_llm(self) -> dict[str, Any]:
        self.finish_llm()
        index = len(self.log["iterations"]) + 1
        llm_input = dict(self._base_input)
        iteration = {
            "id": str(_uuid.uuid4()),
            "index": index,
            "created_at": self._now_iso(),
            "llm": {
                "id": str(_uuid.uuid4()),
                "model": self._model,
                "status": "running",
                "input": llm_input,
                "output": "",
                "reasoning_content": "",
                "tokens": 0,
                "elapsed_time": 0,
                "error": None,
                "_started_at": time.time(),
            },
            "tool_calls": [],
        }
        self.log["iterations"].append(iteration)
        self._current_iteration = iteration
        self._current_llm = iteration["llm"]
        self.log["meta"]["iterations"] = len(self.log["iterations"])
        return iteration

    def ensure_iteration(self) -> dict[str, Any]:
        if not self._current_iteration:
            return self.start_llm()
        return self._current_iteration

    def append_llm_chunk(self, *, output: str = "", reasoning: str = "") -> None:
        llm = self.ensure_iteration()["llm"]
        if output:
            llm["output"] = f"{llm.get('output', '')}{output}"
        if reasoning:
            llm["reasoning_content"] = f"{llm.get('reasoning_content', '')}{reasoning}"

    def finish_llm(self, *, output: str = "", tokens: int = 0, error: str | None = None) -> None:
        llm = self._current_llm
        if not llm or llm.get("status") != "running":
            return
        if output and not llm.get("output"):
            llm["output"] = output
        if tokens:
            llm["tokens"] = tokens
            self.log["meta"]["total_tokens"] = int(self.log["meta"].get("total_tokens") or 0) + tokens
        llm["status"] = "error" if error else "success"
        llm["error"] = error
        llm["elapsed_time"] = round(time.time() - llm.get("_started_at", time.time()), 3)

    def start_tool(self, *, step_id: str, tool_name: str, tool_input: Any, meta: dict[str, Any] | None) -> dict[str, Any]:
        iteration = self.ensure_iteration()
        tool_call = {
            "id": step_id,
            "status": "running",
            "tool_name": tool_name,
            "tool_label": (meta or {}).get("label") or (meta or {}).get("name") or tool_name,
            "tool_input": self._jsonable(tool_input),
            "tool_output": None,
            "tool_parameters": (meta or {}).get("tool_parameters", {}),
            "tool_icon": (meta or {}).get("icon") or (meta or {}).get("icon_url"),
            "time_cost": 0,
            "error": None,
            "meta": self._jsonable(meta or {}),
            "_started_at": time.time(),
        }
        iteration["tool_calls"].append(tool_call)
        self._tools_by_id[step_id] = tool_call
        return tool_call

    def finish_tool(
            self,
            *,
            step_id: str | None,
            tool_name: str,
            status: str,
            output: Any = None,
            error: str | None = None,
            meta: dict[str, Any] | None = None,
    ) -> None:
        tool_call = self._tools_by_id.get(step_id or "")
        if not tool_call:
            for iteration in reversed(self.log["iterations"]):
                for item in reversed(iteration.get("tool_calls") or []):
                    if item.get("tool_name") == tool_name and item.get("status") == "running":
                        tool_call = item
                        break
                if tool_call:
                    break
        if not tool_call:
            return
        tool_call["status"] = status
        tool_call["tool_output"] = self._jsonable(output)
        tool_call["error"] = error
        if meta is not None:
            tool_call["meta"] = self._jsonable(meta)
        tool_call["time_cost"] = round(time.time() - tool_call.get("_started_at", time.time()), 3)

    def finalize(self, *, status: str = "success", total_tokens: int = 0, error: str | None = None) -> dict[str, Any]:
        final_error = error or (None if status == "success" else status)
        self.finish_llm(tokens=0, error=final_error)
        self.log["meta"]["status"] = status
        self.log["meta"]["elapsed_time"] = round(time.time() - self._started_at, 3)
        if total_tokens:
            self.log["meta"]["total_tokens"] = total_tokens
        if final_error:
            self.log["meta"]["error"] = final_error
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        self.log["meta"]["elapsed_time"] = round(time.time() - self._started_at, 3)
        self.log["meta"]["iterations"] = len(self.log["iterations"])
        return self._strip_private(self.log)


class LangChainAgent:

    def __init__(
            self,
            model_name: str,
            provider: str = "openai",
            temperature: float = 0.7,
            max_tokens: int = 2000,
            system_prompt: Optional[str] = None,
            tools: Optional[Sequence[BaseTool]] = None,
            streaming: bool = False,
            top_p: Optional[float] = None,
            top_k: Optional[int] = None,
            seed: Optional[int] = None,
            repetition_penalty: Optional[float] = None,
            frequency_penalty: Optional[float] = None,
            presence_penalty: Optional[float] = None,
            enable_search: bool = False,
            stop: Optional[List[str]] = None,
            extra_headers: Optional[Dict[str, Any]] = None,
            max_iterations: Optional[int] = None,  # Max iterations (None means auto-calculate)
            strategy: str = "react",  # Agent execution strategy: 'react' or 'function_calling'
            deep_thinking: bool = False,  # Whether deep-thinking mode is enabled
            thinking_budget_tokens: Optional[int] = None,  # Deep-thinking token budget
            json_output: bool = False,  # Whether to force JSON output
            tool_call_limit: Optional[int] = None,  # Max calls per tool; None = AGENT_TOOL_CALL_LIMIT
            context_evidence: Optional[List[Any]] = None,
            context_query: str = "",
            context_base_text: str = "",
            context_evidence_loader: Optional[Any] = None,
            model_view: Optional["ModelInfo"] = None,  # Remote mode: undecrypted runtime model view
            executed_tool_names: Optional[Sequence[str]] = None,  # Tools already executed upstream (ToolOrchestrator)
    ):
        """Initialize the LangChain Agent

        Args:
            model_name: Model name
            provider: Provider (openai, xinference, gpustack, ollama, dashscope)
            temperature: Temperature parameter
            max_tokens: Max token count
            system_prompt: System prompt
            tools: Tool list (optional; the framework runs the ReAct loop automatically)
            streaming: Whether to enable streaming output
            max_iterations: Max iterations (None means auto-calculate, based on LangGraph graph steps)
            strategy: Agent execution strategy ('react' uses the ReAct reasoning loop,
                'function_calling' uses native tool calling)
            tool_call_limit: Max calls per tool; call N+1 is rejected with a hint telling
                the model to answer directly. None uses the AGENT_TOOL_CALL_LIMIT setting
            executed_tool_names: tools already executed upstream on the Agent's behalf
                (weak-model ToolOrchestrator); they are declared in the methodology as
                already-executed instead of as invocable tools
        """
        self.model_name = model_name
        self.provider = provider
        self.tools = tools or []
        self.streaming = streaming
        self.strategy = strategy
        self.tool_call_limit = (
            tool_call_limit if tool_call_limit is not None else default_tool_call_limit()
        )
        self._initial_context_evidence = list(context_evidence or [])
        self._context_query = context_query
        self._context_base_text = context_base_text
        self._context_evidence_loader = context_evidence_loader
        from ..services.context_assembler import resolve_evidence_max_tokens
        self._evidence_max_tokens = resolve_evidence_max_tokens(max_tokens)

        # Build a tool-name -> metadata map (enriches execution records with resource details)
        self._tool_meta_map: Dict[str, Dict[str, Any]] = {}
        for t in self.tools:
            tool_name = getattr(t, "name", None)
            tool_meta = getattr(t, "_tool_meta", None)
            if tool_name and tool_meta:
                self._tool_meta_map[tool_name] = tool_meta

        # Wrap each tool with a call-count limit (cap each tool at N calls; an
        # over-limit call returns an in-band message steering the model to answer from
        # what it already has — it never aborts the run)
        self._tool_call_counts: Dict[str, int] = {}
        self._wrap_tools_with_call_limit(self.tool_call_limit)

        # Adjust the max iteration count dynamically based on the tool count.
        # LangGraph's recursion_limit counts super-steps: each model decision and each
        # tool batch is one. The deterministic budget is "every tool used up to its
        # per-tool limit" plus an LLM decision before/after each batch, with a safety
        # slack so a research run that spends its whole budget still reaches the final
        # answer instead of dying one step before it.
        if max_iterations is None:
            self.max_iterations = self._compute_recursion_limit(
                len(self.tools), self.tool_call_limit
            )
        else:
            self.max_iterations = max_iterations

        # The user-configured role / system prompt is kept verbatim, followed by an
        # 【执行要求】 section generated dynamically from the tools actually assigned
        # (no code-interpreter assignment means no code-execution capability claimed);
        # with no user prompt, a default role that declares no capability is used.
        raw_system_prompt = (system_prompt or "").strip() or DEFAULT_AGENT_ROLE_PROMPT
        tool_names = [n for n in (getattr(t, "name", None) for t in self.tools) if n]
        methodology = build_agent_methodology(
            tool_names, executed_tool_names or (), tool_call_limit=self.tool_call_limit
        )
        self.system_prompt = f"{raw_system_prompt}\n\n{methodology}"

        # Inject a JSON prompt fallback for all providers so JSON output is still guided
        # even when the API-level response_format has no effect
        if json_output:
            self.system_prompt += "\n请仅输出一个合法JSON对象，不要输出Markdown代码块或额外说明。"

        logger.debug(
            f"Agent 迭代次数配置: max_iterations={self.max_iterations}, "
            f"tool_count={len(self.tools)}, "
            f"auto_calculated={max_iterations is None}"
        )

        # Filter per-request parameters through the allowlist (contract enforced service-side;
        # unknown keys are dropped by the remote adapter)
        extra_params: Dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "streaming": streaming,
        }
        optional_params = {
            "top_p": top_p,
            "top_k": top_k,
            "seed": seed,
            "repetition_penalty": repetition_penalty,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
        }
        for key, value in optional_params.items():
            if value is not None:
                extra_params[key] = value
        if enable_search:
            extra_params["enable_search"] = True
        if stop:
            extra_params["stop"] = stop[:4]
        if extra_headers:
            extra_params["default_headers"] = extra_headers

        if model_view is None:
            raise ValueError("LangChainAgent 需要远端模型视图（model_view）")
        # Remote mode: the host carries only config references and per-request parameters;
        # credential decryption, routing, channel failover, and capability arbitration
        # all live in the model service.
        remote_params: Dict[str, Any] = {
            key: value for key, value in extra_params.items() if key != "streaming"
        }
        remote_params["deep_thinking"] = deep_thinking
        if thinking_budget_tokens is not None:
            remote_params["thinking_budget_tokens"] = thinking_budget_tokens
        remote_params["json_output"] = json_output
        self.llm = RedBearChatModel.for_invoke(
            model_view, params=remote_params, streaming=streaming
        )
        # Thinking-state detection stays in the host (used to strip reasoning content);
        # capability arbitration is executed service-side from profile facts
        self.deep_thinking = deep_thinking
        self.json_output = json_output

        self._wrap_tools_with_external_context()

        # Grab the underlying model for the actual streaming calls
        self._underlying_llm = self.llm._model if hasattr(self.llm, '_model') else self.llm

        # Make sure the underlying model also has streaming enabled
        if streaming and hasattr(self._underlying_llm, 'streaming'):
            self._underlying_llm.streaming = True

        # Create the agent graph via create_agent (the standard LangChain 1.x approach)
        # Handle everything through the agent whether or not tools are present
        self.agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.system_prompt
        )

        logger.info(
            "LangChain Agent 初始化完成",
            extra={
                "model": model_name,
                "provider": provider,
                "temperature": temperature,
                "streaming": streaming,
                "max_iterations": self.max_iterations,
                "strategy": self.strategy,
                "tool_count": len(self.tools),
                "tool_names": [tool.name for tool in self.tools] if self.tools else [],
                "tool_call_limit": self.tool_call_limit,
            }
        )

    def _wrap_tools_with_external_context(self) -> None:
        """Assemble per-tool evidence and append it to the tool return value so it enters the ToolMessage."""
        if not self.tools:
            return
        from ..services.context_assembler import (
            ContextAssembler,
            context_evidence_from_tool_result,
            create_evidence_compressor,
        )

        assembly_lock = asyncio.Lock()
        pending_shared_evidence = list(self._initial_context_evidence)
        loaded_annotations = False

        assembler = ContextAssembler(
            self._evidence_max_tokens,
            compressor=create_evidence_compressor(self.llm, self._content_to_text),
        )
        for tool in self.tools:
            # ToolNode drives native tools through BaseTool.ainvoke().  Do not
            # only wrap _arun: synchronous StructuredTool instances are
            # otherwise executed through _run and bypass the assembly hook.
            original_ainvoke = getattr(tool, "ainvoke", None)
            if not callable(original_ainvoke):
                continue

            def make_ainvoke(current_tool, original):
                @functools.wraps(original)
                async def wrapped(*args, **kwargs):
                    nonlocal loaded_annotations
                    result = await original(*args, **kwargs)
                    evidence = getattr(current_tool, "_context_evidence", None)
                    if not evidence:
                        tool_input = args[0] if args and isinstance(args[0], dict) else None
                        evidence = context_evidence_from_tool_result(current_tool, result, tool_input)
                    if evidence:
                        # Native ToolNode may execute several tool calls in
                        # parallel. Keep each ToolMessage scoped to its own
                        # evidence; otherwise the second result repeats all
                        # evidence already returned by the first result.
                        async with assembly_lock:
                            evidence_for_message = list(evidence)
                            current_tool._context_evidence = []
                            if pending_shared_evidence:
                                evidence_for_message.extend(pending_shared_evidence)
                                pending_shared_evidence.clear()
                            if not loaded_annotations and self._context_evidence_loader:
                                try:
                                    extra = self._context_evidence_loader()
                                    if inspect.isawaitable(extra):
                                        extra = await asyncio.wait_for(
                                            extra, timeout=EVIDENCE_ASSEMBLY_TIMEOUT_S
                                        )
                                except Exception as exc:  # noqa: BLE001 - never lose the tool result
                                    logger.warning(
                                        "[上下文组装] 证据加载失败，忽略 | 工具=%s | %s",
                                        current_tool.name, type(exc).__name__,
                                    )
                                    extra = []
                                evidence_for_message.extend(extra or [])
                                loaded_annotations = True
                            try:
                                assembled = await asyncio.wait_for(
                                    assembler.assemble(
                                        evidence_for_message,
                                        query=self._context_query,
                                        base_text=self._context_base_text,
                                        triggered_by=[current_tool.name],
                                    ),
                                    timeout=EVIDENCE_ASSEMBLY_TIMEOUT_S,
                                )
                            except Exception as exc:  # noqa: BLE001 - never lose the tool result
                                # Assembly is an enhancement; if it fails or hangs the model
                                # must still receive the raw tool result.
                                logger.warning(
                                    "[上下文组装] 组装失败或超时，回退原始工具结果 | 工具=%s | %s",
                                    current_tool.name, type(exc).__name__,
                                )
                                return result
                        current_tool._context_assembly_stats = {
                            "triggered_by": assembled.triggered_by,
                            "evidence_count": len(assembled.evidence),
                            "compressed_count": len(assembled.compressed_evidence_keys),
                            "dropped_count": len(assembled.dropped_evidence),
                            "estimated_tokens": assembled.estimated_tokens,
                        }
                        if assembled.context_text:
                            if isinstance(result, ToolMessage):
                                return result.model_copy(
                                    update={"content": assembled.context_text}
                                )
                            logger.warning(
                                "[上下文组装] 原生工具返回值不是 ToolMessage，"
                                "无法安全替换注入内容 | "
                                f"工具={current_tool.name} | 类型={type(result).__name__}"
                            )
                            return result
                    logger.info(
                        "[上下文组装] 原生 Function Call 工具未产生可组装证据 | "
                        f"工具={current_tool.name} | 保留原始工具结果"
                    )
                    return result
                return wrapped
            object.__setattr__(tool, "ainvoke", make_ainvoke(tool, original_ainvoke))

    @staticmethod
    def _close_running_tool_steps(node_executions, trace, reason: str) -> List[Dict[str, Any]]:
        """Settle every tool step still 'running' as failed and build the matching tool_error events.

        A tool_start without a tool_end/tool_error leaves the frontend spinning forever;
        callers yield the returned events so each started step always gets a terminal one.
        """
        events: List[Dict[str, Any]] = []
        for node in node_executions:
            if node.get("status") != "running":
                continue
            node["status"] = "failed"
            node["error"] = reason[:500]
            node["elapsed_time"] = round(
                (time.time() - node.get("started_at", time.time())) * 1000, 2
            )
            try:
                trace.finish_tool(
                    step_id=node.get("step_id"),
                    tool_name=node.get("node_name", "unknown"),
                    status="error",
                    error=node["error"],
                    meta=node.get("meta"),
                )
            except Exception as exc:  # noqa: BLE001 - trace bookkeeping must not block settling
                logger.warning("收尾工具步骤时 trace 更新失败: %s", type(exc).__name__)
            events.append(
                {
                    "type": "tool_error",
                    "step_id": node.get("step_id"),
                    "name": node.get("node_name", "unknown"),
                    "error": node["error"],
                }
            )
        return events

    def _needs_closing_answer(self, content: str, node_executions) -> bool:
        """Whether this run ended on a narration instead of an answer.

        Symptom being fixed: the model writes "我将为您搜索……" (often along with tool
        calls), the loop then ends for some reason, and that sentence becomes the whole
        answer — recorded as a success and handed to a cluster supervisor as a usable
        result, which makes the supervisor re-dispatch the same task.

        Only runs that actually called tools are candidates: a bare agent's short reply
        is a legitimate answer, and a long reply ending in a colon is a formatted report,
        not a promise.
        """
        if not node_executions:
            return False
        text = (content or "").strip()
        if not text:
            return True
        if len(text) < CLOSING_ANSWER_MIN_CHARS:
            return True
        return text.endswith(("：", ":", "…", "……"))

    def _closing_answer_messages(self, messages: Sequence[BaseMessage]) -> List[BaseMessage]:
        """History for the extra tool-free turn, with an explicit "no more tools" note."""
        history = [m for m in (messages or []) if isinstance(m, BaseMessage)]
        # A trailing tool-call AIMessage (empty content) only repeats the narration;
        # dropping it keeps the closing turn from copying the tool call again.
        while history and isinstance(history[-1], AIMessage) and not self._content_to_text(history[-1].content).strip():
            history.pop()
        return [
            SystemMessage(content=f"{self.system_prompt}{CLOSING_ANSWER_INSTRUCTION}"),
            *history,
            HumanMessage(content="请直接给出最终答案。"),
        ]

    async def _closing_answer_stream(
        self,
        messages: Sequence[BaseMessage],
        trace: "AgentTraceRecorder",
        stats: Dict[str, Any],
    ):
        """Stream one extra tool-free turn that turns the narration into an answer.

        Failure is not fatal: callers keep whatever text the main loop produced.
        """
        prompt = self._closing_answer_messages(messages)
        trace.start_llm()
        produced = ""
        tokens = 0
        async for chunk in self._underlying_llm.astream(prompt):
            text = self._content_to_text(getattr(chunk, "content", chunk))
            if text:
                produced += text
                trace.append_llm_chunk(output=text)
                yield text
            tokens = self._extract_tokens_from_message(chunk) or tokens
        trace.finish_llm(output=produced, tokens=tokens)
        stats["tokens"] = tokens

    async def _closing_answer(
        self,
        messages: Sequence[BaseMessage],
        trace: "AgentTraceRecorder",
    ) -> tuple[str, int]:
        """Non-streaming counterpart of :meth:`_closing_answer_stream`."""
        prompt = self._closing_answer_messages(messages)
        trace.start_llm()
        result = await self._underlying_llm.ainvoke(prompt)
        text = self._content_to_text(getattr(result, "content", result)).strip()
        tokens = self._extract_tokens_from_message(result)
        trace.finish_llm(output=text, tokens=tokens)
        return text, tokens

    def _evidence_digest(self, node_executions) -> str:
        """Rebuild usable evidence from step records.

        An aborted run has no LangGraph message list left (``astream_events`` raised),
        so the closing turn cannot see the tool results the normal path would carry in
        its history. ``node_executions`` still holds every finished tool output, which
        is enough: the observed failure had three successful searches in hand and threw
        the run away because the fourth was rejected.
        """
        chunks: List[str] = []
        used = 0
        for node in node_executions or []:
            if node.get("status") not in ("completed", "success"):
                continue
            output = node.get("output")
            if not output:
                continue
            text = output if isinstance(output, str) else str(output)
            # An over-limit rejection notice is a message to the model, not evidence.
            if _TOOL_REJECTION_MARKER in text:
                continue
            # A tool that returns ToolResult.error_result still closes via on_tool_end,
            # so its step is "completed" even though the body is a failure payload —
            # never feed that to the rescue turn as if it were usable data.
            if '"success": false' in text or '"success":false' in text:
                continue
            text = text[:CLOSING_ANSWER_EVIDENCE_PER_ITEM]
            if used + len(text) > CLOSING_ANSWER_EVIDENCE_TOTAL:
                text = text[: max(0, CLOSING_ANSWER_EVIDENCE_TOTAL - used)]
            if not text.strip():
                break
            chunks.append(f"[{node.get('node_name') or 'tool'}] {text}")
            used += len(text)
            if used >= CLOSING_ANSWER_EVIDENCE_TOTAL:
                break
        return "\n\n".join(chunks)

    def _evidence_closing_messages(
        self,
        messages: Sequence[BaseMessage],
        evidence: str,
    ) -> List[BaseMessage]:
        return [
            SystemMessage(content=f"{self.system_prompt}{ABORTED_CLOSING_ANSWER_INSTRUCTION}"),
            *[m for m in (messages or []) if isinstance(m, BaseMessage)],
            HumanMessage(
                content=f"【已成功获取的工具结果】\n{evidence}\n\n请基于以上内容直接给出最终答案。"
            ),
        ]

    async def _closing_answer_from_evidence_stream(
        self,
        messages: Sequence[BaseMessage],
        node_executions,
        trace: "AgentTraceRecorder",
        stats: Dict[str, Any],
    ):
        """Stream a report built from the evidence an aborted run already collected.

        Yields nothing when there is no usable evidence — the caller then falls back to
        the plain abort notice. Trace bookkeeping settles even if the model call fails.
        """
        evidence = self._evidence_digest(node_executions)
        if not evidence:
            return
        prompt = self._evidence_closing_messages(messages, evidence)
        trace.start_llm()
        produced = ""
        tokens = 0
        try:
            async for chunk in self._underlying_llm.astream(prompt):
                text = self._content_to_text(getattr(chunk, "content", chunk))
                if text:
                    produced += text
                    trace.append_llm_chunk(output=text)
                    yield text
                tokens = self._extract_tokens_from_message(chunk) or tokens
        finally:
            trace.finish_llm(output=produced, tokens=tokens)
            stats["tokens"] = tokens

    async def _closing_answer_from_evidence(
        self,
        messages: Sequence[BaseMessage],
        node_executions,
        trace: "AgentTraceRecorder",
    ) -> tuple[str, int]:
        """Non-streaming counterpart of :meth:`_closing_answer_from_evidence_stream`."""
        evidence = self._evidence_digest(node_executions)
        if not evidence:
            return "", 0
        prompt = self._evidence_closing_messages(messages, evidence)
        trace.start_llm()
        result = await self._underlying_llm.ainvoke(prompt)
        text = self._content_to_text(getattr(result, "content", result)).strip()
        tokens = self._extract_tokens_from_message(result)
        trace.finish_llm(output=text, tokens=tokens)
        return text, tokens

    @staticmethod
    def _compute_recursion_limit(
        tool_count: int,
        per_tool_limit: int,
        slack: int = RECURSION_LIMIT_SAFETY_SLACK,
    ) -> int:
        """LangGraph recursion_limit (super-steps) needed for a full run.

        Worst-case sequential flow spends two super-steps per tool call (one model
        decision, one tool execution); every tool may be used up to its per-tool limit.
        Two more model steps frame the turn (opening decision and final answer); the
        slack covers evidence-assembly middleware and intermediate reasoning so a
        research run that spends its whole tool budget still finishes.
        """
        if tool_count <= 0:
            return 2 + slack
        return 2 + 2 * tool_count * per_tool_limit + slack

    def _over_limit_reply(self, t_name: str, max_calls: int, count: int) -> str:
        """In-band reply for an over-limit tool call.

        The run is NEVER aborted here: killing the graph at this point is exactly what
        discarded runs whose tool calls had already returned the data the model needed
        (the observed research run had three successful searches and was assembling the
        report). The tool keeps returning this message, so the model is told in plain
        terms that calling it again cannot produce anything new and it must answer now.
        """
        logger.warning(
            f"工具 {t_name} 已达到最大调用次数限制 ({max_calls})，"
            f"本次（第 {count} 次）不执行，仅返回收尾提示"
        )
        return (
            f"[工具调用限制] 工具 '{t_name}' 在本轮已调用 {max_calls} 次，达到上限；"
            "本轮内再次调用它也不会真正执行，只会得到本提示。"
            "请立即停止调用该工具，直接基于已有信息给出最终答案，"
            "并把缺少的信息用一句话如实说明，不要重试。"
        )

    def _wrap_tools_with_call_limit(self, max_calls: int) -> None:
        """Wrap each tool with a call-count limit

        Overrides the tool's _run / _arun methods to increment a counter before each
        execution; once a tool's cumulative call count exceeds max_calls, the tool is no
        longer executed and a hint message is returned instead, guiding the LLM to give
        a final answer instead of looping on the same tool.

        Args:
            max_calls: Max call count allowed per tool
        """
        if not self.tools:
            return

        for tool in self.tools:
            tool_name = getattr(tool, "name", None) or str(id(tool))
            original_run = tool._run
            original_arun = getattr(tool, "_arun", None)

            # Wrap the synchronous path
            # Note: functools.wraps must preserve the original signature, otherwise
            # BaseTool.run checks for the "config" / "run_manager" parameters via
            # inspect.signature before calling self._run; without the signature
            # LangChain will not inject config, and StructuredTool._run raises
            # TypeError: missing 1 required keyword-only argument: 'config'
            def _make_wrapped_run(t_name: str, orig):
                @functools.wraps(orig)
                def wrapped_run(*args, **kwargs):
                    self._tool_call_counts[t_name] = (
                        self._tool_call_counts.get(t_name, 0) + 1
                    )
                    count = self._tool_call_counts[t_name]
                    logger.info(
                        f"工具调用计数: tool={t_name}, count={count}/{max_calls}"
                    )
                    if count > max_calls:
                        return self._over_limit_reply(t_name, max_calls, count)
                    return orig(*args, **kwargs)

                return wrapped_run

            # Wrap the asynchronous path
            def _make_wrapped_arun(t_name: str, orig):
                if orig is None:
                    return None

                @functools.wraps(orig)
                async def wrapped_arun(*args, **kwargs):
                    self._tool_call_counts[t_name] = (
                        self._tool_call_counts.get(t_name, 0) + 1
                    )
                    count = self._tool_call_counts[t_name]
                    logger.info(
                        f"工具调用计数: tool={t_name}, count={count}/{max_calls}"
                    )
                    if count > max_calls:
                        return self._over_limit_reply(t_name, max_calls, count)
                    return await orig(*args, **kwargs)

                return wrapped_arun

            tool._run = _make_wrapped_run(tool_name, original_run)
            wrapped_arun = _make_wrapped_arun(tool_name, original_arun)
            if wrapped_arun is not None:
                tool._arun = wrapped_arun

        logger.debug(
            f"工具调用次数限制已启用: max_calls_per_tool={max_calls}, "
            f"tool_names={[t.name for t in self.tools]}"
        )

    def _prepare_messages(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            files: Optional[List[Dict[str, Any]]] = None
    ) -> List[BaseMessage]:
        """Prepare the message list

        Args:
            message: User message
            history: History message list
            context: Context information
            files: Multimodal file content list (already processed)

        Returns:
            List[BaseMessage]: Message list
        """
        messages: list = []

        # Append history messages
        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        # Append the current user message
        user_content = message
        if context:
            user_content = f"参考信息：\n{context}\n\n用户问题：\n{user_content}"

        # Build the user message (multimodal supported)
        if files and len(files) > 0:
            content_parts = self._build_multimodal_content(user_content, files)
            messages.append(HumanMessage(content=content_parts))
        else:
            # Plain-text message
            messages.append(HumanMessage(content=user_content))

        return messages

    @staticmethod
    def _extract_tokens_from_message(msg) -> int:
        """Extract total_tokens from an AIMessage or similar object, supporting several provider formats

        Supported formats:
        - response_metadata.token_usage.total_tokens (OpenAI/ChatOpenAI)
        - response_metadata.usage.total_tokens (some providers)
        - usage_metadata.total_tokens (newer LangChain)
        """
        total = 0
        # 1. response_metadata
        response_meta = getattr(msg, "response_metadata", None)
        if response_meta and isinstance(response_meta, dict):
            # Try the token_usage path
            token_usage = response_meta.get("token_usage") or response_meta.get("usage", {})
            if isinstance(token_usage, dict):
                total = token_usage.get("total_tokens", 0)
        # 2. usage_metadata (newer LangChain AIMessage attribute)
        if not total:
            usage_meta = getattr(msg, "usage_metadata", None)
            if usage_meta:
                if isinstance(usage_meta, dict):
                    total = usage_meta.get("total_tokens", 0)
                else:
                    total = getattr(usage_meta, "total_tokens", 0)
        return total or 0

    def _build_multimodal_content(self, text: str, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Build multimodal message content

        Args:
            text: Text content
            files: File list (already converted by MultimodalService to the provider's format)

        Returns:
            List[Dict]: Message content list
        """
        content_parts = [{"type": "text", "text": text}]

        # Append file content
        # MultimodalService already returns the correct per-provider format, use as-is
        content_parts.extend(files)

        logger.debug(
            f"构建多模态消息: provider={self.provider}, "
            f"parts={len(content_parts)}, "
            f"files={len(files)}"
        )

        return content_parts

    @staticmethod
    def _extract_reasoning_content(msg) -> str:
        """Extract the deep-thinking content (reasoning_content) from an AIMessage

        All providers deliver it uniformly via additional_kwargs.reasoning_content:
        - DeepSeek-R1 / QwQ: native field
        - Volcano (Doubao-thinking): injected by VolcanoChatOpenAI from delta.reasoning_content
        """
        additional = getattr(msg, "additional_kwargs", None) or {}
        return additional.get("reasoning_content") or additional.get("reasoning", "")

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        text_parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "text":
                        text_parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            return "".join(text_parts)
        return str(content)

    @staticmethod
    def _extract_message_from_event_output(output: Any) -> AIMessage | None:
        if isinstance(output, AIMessage):
            return output
        if isinstance(output, dict):
            value = output.get("message") or output.get("output")
            if isinstance(value, AIMessage):
                return value
            generations = output.get("generations")
            if generations and isinstance(generations, list):
                for generation_group in generations:
                    items = generation_group if isinstance(generation_group, list) else [generation_group]
                    for generation in items:
                        message = getattr(generation, "message", None)
                        if isinstance(message, AIMessage):
                            return message
        message = getattr(output, "message", None)
        if isinstance(message, AIMessage):
            return message
        return None

    @staticmethod
    def _truncate_data(data, max_length: int = 2000) -> Any:
        if data is None:
            return None
        if isinstance(data, str):
            return data[:max_length] + "..." if len(data) > max_length else data
        try:
            text = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(data)
        return text[:max_length] + "...[truncated]" if len(text) > max_length else text

    def _extract_tool_sources(self, tool_name: str, node: Dict[str, Any]) -> None:
        """Extract source information from the tool instance and merge it into node["meta"]"""
        for t in self.tools:
            t_name = getattr(t, "name", None)
            if t_name == tool_name:
                sources = getattr(t, "_last_sources", None)
                context_stats = getattr(t, "_context_assembly_stats", None)
                if sources:
                    if not node.get("meta"):
                        node["meta"] = {}
                    node["meta"]["sources"] = sources
                    t._last_sources = []
                if context_stats:
                    if not node.get("meta"):
                        node["meta"] = {}
                    node["meta"]["context_assembly"] = context_stats
                    t._context_assembly_stats = None
                break

    async def chat(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            files: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Run a chat turn

        Args:
            message: User message
            history: History message list [{"role": "user/assistant", "content": "..."}]
            context: Context information (e.g. knowledge base retrieval results)
            files: Multimodal files

        Returns:
            Dict: Dictionary containing content, node_executions, and metadata
        """
        start_time = time.time()
        # Collect node execution records
        node_executions: List[Dict[str, Any]] = []
        # Temporary stack used to match tool_start/tool_end events
        _tool_stack: List[Dict[str, Any]] = []

        try:
            # Prepare the message list (multimodal supported)
            messages = self._prepare_messages(message, history, context, files)
            trace = AgentTraceRecorder(
                model=self.model_name,
                strategy=self.strategy,
                system_prompt=self.system_prompt,
                messages=messages,
            )

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "has_context": bool(context),
                    "has_history": bool(history),
                    "has_tools": bool(self.tools),
                    "has_files": bool(files),
                    "message_count": len(messages),
                    "max_iterations": self.max_iterations
                }
            )

            # Use astream_events to capture intermediate steps
            content = ""
            total_tokens = 0
            reasoning_content = ""
            last_event = {}

            try:
                async for event in self.agent.astream_events(
                    {"messages": messages},
                    version="v2",
                    config={"recursion_limit": self.max_iterations}
                ):
                    last_event = event
                    kind = event.get("event")

                    if kind in ("on_chat_model_start", "on_llm_start"):
                        trace.start_llm()

                    elif kind in ("on_chat_model_end", "on_llm_end"):
                        output_message = self._extract_message_from_event_output(event.get("data", {}).get("output"))
                        output_text = self._content_to_text(output_message.content) if output_message else ""
                        tokens = self._extract_tokens_from_message(output_message) if output_message else 0
                        trace.finish_llm(output=output_text, tokens=tokens)

                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_input = event.get("data", {}).get("input")
                        step_id = str(_uuid.uuid4())
                        tool_meta = self._tool_meta_map.get(tool_name, {})
                        node = {
                            "step_id": step_id,
                            "node_type": "tool",
                            "node_name": tool_name,
                            "status": "running",
                            "input": self._truncate_data(tool_input),
                            "output": None,
                            "elapsed_time": 0,
                            "started_at": time.time(),
                            "error": None,
                            "meta": tool_meta if tool_meta else None,
                        }
                        _tool_stack.append(node)
                        node_executions.append(node)
                        trace.start_tool(
                            step_id=step_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            meta=tool_meta if tool_meta else None,
                        )

                    elif kind == "on_tool_end":
                        tool_output = event.get("data", {}).get("output")
                        tool_name = event.get("name", "unknown")
                        matched_node = None
                        if _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        if matched_node:
                            matched_node["status"] = "completed"
                            matched_node["output"] = self._truncate_data(tool_output)
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )
                            # Extract extra source info from the tool (e.g. knowledge base retrieval sources)
                            self._extract_tool_sources(tool_name, matched_node)

                        trace.finish_tool(
                            step_id=matched_node.get("step_id") if matched_node else None,
                            tool_name=tool_name,
                            status="success",
                            output=tool_output,
                            meta=matched_node.get("meta") if matched_node else None,
                        )

                    elif kind == "on_tool_error":
                        error_msg = str(event.get("data", {}).get("error", ""))
                        tool_name = event.get("name", "unknown")
                        matched_node = None
                        if _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        if matched_node:
                            matched_node["status"] = "failed"
                            matched_node["error"] = error_msg[:500]
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )

                        trace.finish_tool(
                            step_id=matched_node.get("step_id") if matched_node else None,
                            tool_name=tool_name,
                            status="error",
                            error=error_msg[:500],
                            meta=matched_node.get("meta") if matched_node else None,
                        )

                    elif kind == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content"):
                            if self.deep_thinking:
                                rc = self._extract_reasoning_content(chunk)
                                if rc:
                                    reasoning_content += rc
                                    trace.append_llm_chunk(reasoning=rc)
                            chunk_content = chunk.content
                            chunk_text = self._content_to_text(chunk_content)
                            if chunk_text:
                                content += chunk_text
                                trace.append_llm_chunk(output=chunk_text)

                # Extract token statistics from the last event
                output_messages = last_event.get("data", {}).get("output", {}).get("messages", [])
                for msg in reversed(output_messages):
                    if isinstance(msg, AIMessage):
                        total_tokens = self._extract_tokens_from_message(msg)
                        if not content:
                            # If streaming produced no content, extract it from the final message
                            if isinstance(msg.content, str):
                                content = msg.content
                            elif isinstance(msg.content, list):
                                text_parts = []
                                for item in msg.content:
                                    if isinstance(item, dict):
                                        if "text" in item:
                                            text_parts.append(item.get("text", ""))
                                        elif item.get("type") == "text":
                                            text_parts.append(item.get("text", ""))
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                content = "".join(text_parts)
                            else:
                                content = str(msg.content)
                        if self.deep_thinking and not reasoning_content:
                            reasoning_content = self._extract_reasoning_content(msg)
                        break

            except (RecursionError, GraphRecursionError) as e:
                # The graph burned its step budget before the model produced a final
                # answer. The run genuinely did not finish, so it stays a failure, but
                # successful tool outputs are still in node_executions — give the model
                # one tool-free turn to turn them into a real answer. A failed turn
                # that carries a real answer is usable; only a turn with no evidence
                # degrades to the plain abort notice.
                abort_reason = (
                    f"执行在达到最大处理步骤限制（{self.max_iterations} 步）后仍未收尾"
                )
                logger.warning(
                    "Agent 达到步骤上限，尝试基于已有证据收尾",
                    extra={
                        "reason": "max_iterations",
                        "max_iterations": self.max_iterations,
                        "error": str(e)[:300],
                    },
                )
                # Mark unfinished tools as failed
                for node in _tool_stack:
                    node["status"] = "failed"
                    node["error"] = abort_reason[:500]
                    node["elapsed_time"] = round(
                        (time.time() - node.get("started_at", time.time())) * 1000, 2
                    )
                _tool_stack.clear()

                # Strip started_at (not needed in the persisted output)
                for node in node_executions:
                    node.pop("started_at", None)

                rescued_text, rescued_tokens = "", 0
                try:
                    rescued_text, rescued_tokens = await self._closing_answer_from_evidence(
                        messages, node_executions, trace
                    )
                except Exception as rescue_exc:  # noqa: BLE001 - rescue must not mask the abort
                    logger.warning(
                        "证据救援收尾失败，回落中止文案",
                        extra={"error": str(rescue_exc)[:300]},
                    )
                rescued = bool((rescued_text or "").strip())
                # Non-streaming: nothing was sent yet, so a rescued report replaces the
                # partial narration outright instead of being appended after it.
                final_content = rescued_text if rescued else (content or abort_reason)

                return {
                    "content": final_content,
                    "model": self.model_name,
                    "elapsed_time": time.time() - start_time,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": rescued_tokens,
                    },
                    "node_executions": node_executions,
                    "agent_log": trace.finalize(
                        status="error",
                        error=abort_reason,
                        total_tokens=rescued_tokens,
                    ),
                    "run_error": {
                        "code": "agent_aborted",
                        "reason": "max_iterations",
                        "error": abort_reason,
                        "rescued": rescued,
                    },
                }

            trace.finish_llm(output=content, tokens=total_tokens)
            logger.info(f"最终提取的内容长度: {len(content)}")

            # Same final-answer guard as the streaming path (see _needs_closing_answer):
            # a non-streaming run that ends on a narration gets one tool-free closing turn.
            if self._needs_closing_answer(content, node_executions):
                try:
                    closing_text, closing_tokens = await self._closing_answer(messages, trace)
                except Exception as closing_exc:  # noqa: BLE001 - keep the text we have
                    logger.warning(
                        "收尾生成失败，保留原有正文",
                        extra={"error": str(closing_exc)[:300]},
                    )
                else:
                    if closing_text:
                        content = f"{content}{closing_text}"
                        total_tokens += closing_tokens

            # Strip started_at and make sure every node has a final status
            for node in node_executions:
                node.pop("started_at", None)
                # Still "running" here means the on_tool_end event was lost; mark as completed
                if node.get("status") == "running":
                    node["status"] = "completed"

            elapsed_time = time.time() - start_time
            response = {
                "content": content,
                "model": self.model_name,
                "elapsed_time": elapsed_time,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": total_tokens
                },
                "node_executions": node_executions,
                "agent_log": trace.finalize(status="success", total_tokens=total_tokens),
            }
            if reasoning_content:
                response["reasoning_content"] = reasoning_content

            logger.debug(
                "Agent 调用完成",
                extra={
                    "elapsed_time": elapsed_time,
                    "content_length": len(response["content"]),
                    "node_execution_count": len(node_executions)
                }
            )

            return response

        except Exception as e:
            logger.error("Agent 调用失败", extra={"error": str(e)})
            raise

    async def chat_stream(
            self,
            message: str,
            history: Optional[List[Dict[str, str]]] = None,
            context: Optional[str] = None,
            files: Optional[List[Dict[str, Any]]] = None,
            emit_final_answer: bool = False,
    ) -> AsyncGenerator[str | int | dict[str, Any] | list, None]:
        """Run a streaming chat turn

        Args:
            message: User message
            history: History message list
            context: Context information
            files: Multimodal files
            emit_final_answer: When True, additionally emit an out-of-band
                ``{"type": "final_answer", ...}`` event carrying ONLY the final
                tool-call-free answer (no intermediate narration). This is opt-in and
                used solely by the cluster sub-agent path so the supervisor gets a clean
                tool result; single-Agent apps, workflow agent nodes and the supervisor
                itself leave it False and never receive the event.

        Yields:
            str: Message content chunk
            int: Token statistics
            Dict: Deep-thinking content {"type": "reasoning", "content": "..."}
            list: Node execution records [{"node_type": ..., ...}] (yielded last)
        """
        logger.info("=" * 80)
        logger.info(" chat_stream 方法开始执行")
        logger.info(f"  Message: {message[:100]}")
        logger.info(f"  Has tools: {bool(self.tools)}")
        logger.info(f"  Tool count: {len(self.tools) if self.tools else 0}")
        logger.info("=" * 80)

        # Collect node execution records
        node_executions: List[Dict[str, Any]] = []
        _tool_stack: List[Dict[str, Any]] = []
        # LangChain custom events only carry run lineage; this map attributes events
        # from parallel runs to product step_ids.
        _tool_runs: Dict[str, str] = {}

        try:
            # Prepare the message list (multimodal supported)
            messages = self._prepare_messages(message, history, context, files)
            trace = AgentTraceRecorder(
                model=self.model_name,
                strategy=self.strategy,
                system_prompt=self.system_prompt,
                messages=messages,
            )

            logger.debug(
                f"准备流式调用，has_tools={bool(self.tools)}, has_files={bool(files)}, message_count={len(messages)}"
            )

            chunk_count = 0

            # Use the agent's astream_events uniformly for streaming output
            logger.debug("使用 Agent astream_events 实现流式输出")
            full_content = ''
            full_reasoning = ''
            # The final answer (the LAST tool-call-free AIMessage), tracked separately
            # from full_content. full_content is the concatenation of every streamed
            # model round, including intermediate narration ("我将为您搜索…"); the
            # cluster supervisor only needs the final answer, so it is surfaced out of
            # band via a "final_answer" event instead of receiving the narration.
            final_answer_text = ''
            try:
                last_event = {}
                async for event in self.agent.astream_events(
                        {"messages": messages},
                        version="v2",
                        config={"recursion_limit": self.max_iterations}
                ):
                    last_event = event
                    chunk_count += 1
                    kind = event.get("event")

                    # Handle every possible streaming event
                    if kind in ("on_chat_model_start", "on_llm_start"):
                        trace.start_llm()
                        yield {"type": "agent_log", "data": trace.to_dict()}

                    elif kind in ("on_chat_model_end", "on_llm_end"):
                        output_message = self._extract_message_from_event_output(event.get("data", {}).get("output"))
                        output_text = self._content_to_text(output_message.content) if output_message else ""
                        tokens = self._extract_tokens_from_message(output_message) if output_message else 0
                        trace.finish_llm(output=output_text, tokens=tokens)
                        # The final answer is the last model message that carries text and
                        # no tool call. Intermediate narration rounds (they issue a tool
                        # call) are ignored even when they stream a preamble.
                        if output_message is not None and output_text.strip() and not getattr(
                            output_message, "tool_calls", None
                        ):
                            final_answer_text = output_text
                        yield {"type": "agent_log", "data": trace.to_dict()}

                    elif kind == "on_chat_model_stream":
                        # LLM streaming output
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content"):
                            # Extract deep-thinking content (only when deep thinking is enabled)
                            if self.deep_thinking:
                                reasoning_chunk = self._extract_reasoning_content(chunk)
                                if reasoning_chunk:
                                    full_reasoning += reasoning_chunk
                                    trace.append_llm_chunk(reasoning=reasoning_chunk)
                                    yield {"type": "reasoning", "content": reasoning_chunk}

                            # Handle multimodal responses: content may be a string or a list
                            chunk_content = chunk.content
                            if isinstance(chunk_content, str) and chunk_content:
                                full_content += chunk_content
                                trace.append_llm_chunk(output=chunk_content)
                                yield chunk_content
                            elif isinstance(chunk_content, list):
                                # Multimodal response: extract the text parts
                                for item in chunk_content:
                                    if isinstance(item, dict):
                                        # Qwen format: {"text": "..."}
                                        if "text" in item:
                                            text = item.get("text", "")
                                            if text:
                                                full_content += text
                                                trace.append_llm_chunk(output=text)
                                                yield text
                                        # OpenAI format: {"type": "text", "text": "..."}
                                        elif item.get("type") == "text":
                                            text = item.get("text", "")
                                            if text:
                                                full_content += text
                                                trace.append_llm_chunk(output=text)
                                                yield text
                                    elif isinstance(item, str):
                                        full_content += item
                                        trace.append_llm_chunk(output=item)
                                        yield item

                    elif kind == "on_llm_stream":
                        # Another variant of LLM streaming event
                        chunk = event.get("data", {}).get("chunk")
                        if chunk:
                            if hasattr(chunk, "content"):
                                # Extract deep-thinking content (only when deep thinking is enabled)
                                if self.deep_thinking:
                                    reasoning_chunk = self._extract_reasoning_content(chunk)
                                    if reasoning_chunk:
                                        full_reasoning += reasoning_chunk
                                        trace.append_llm_chunk(reasoning=reasoning_chunk)
                                        yield {"type": "reasoning", "content": reasoning_chunk}

                                chunk_content = chunk.content
                                if isinstance(chunk_content, str) and chunk_content:
                                    full_content += chunk_content
                                    trace.append_llm_chunk(output=chunk_content)
                                    yield chunk_content
                                elif isinstance(chunk_content, list):
                                    # Multimodal response: extract the text parts
                                    for item in chunk_content:
                                        if isinstance(item, dict):
                                            # Qwen format: {"text": "..."}
                                            if "text" in item:
                                                text = item.get("text", "")
                                                if text:
                                                    full_content += text
                                                    trace.append_llm_chunk(output=text)
                                                    yield text
                                            # OpenAI format: {"type": "text", "text": "..."}
                                            elif item.get("type") == "text":
                                                text = item.get("text", "")
                                                if text:
                                                    full_content += text
                                                    trace.append_llm_chunk(output=text)
                                                    yield text
                                        elif isinstance(item, str):
                                            full_content += item
                                            trace.append_llm_chunk(output=item)
                                            yield item
                            elif isinstance(chunk, str):
                                full_content += chunk
                                trace.append_llm_chunk(output=chunk)
                                yield chunk

                    # Record tool calls
                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_input = event.get("data", {}).get("input")
                        step_id = str(_uuid.uuid4())
                        tool_meta = self._tool_meta_map.get(tool_name, {})
                        node = {
                            "step_id": step_id,
                            "node_type": "tool",
                            "node_name": tool_name,
                            "status": "running",
                            "input": self._truncate_data(tool_input),
                            "output": None,
                            "elapsed_time": 0,
                            "started_at": time.time(),
                            "error": None,
                            "meta": tool_meta if tool_meta else None,
                        }
                        _tool_stack.append(node)
                        node_executions.append(node)
                        run_id = event.get("run_id")
                        if run_id:
                            _tool_runs[str(run_id)] = step_id
                        trace.start_tool(
                            step_id=step_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            meta=tool_meta if tool_meta else None,
                        )
                        logger.debug(f"工具调用开始: {tool_name}")
                        # Push the tool-start event in real time
                        yield {"type": "agent_log", "data": trace.to_dict()}
                        yield {"type": "tool_start", "step_id": step_id, "name": tool_name, "input": self._truncate_data(tool_input), "meta": tool_meta if tool_meta else None}
                    elif kind == "on_custom_event" and event.get("name") == "memory_stage":
                        if not is_memory_stage_capture_enabled():
                            continue
                        raw_stage = event.get("data") or {}
                        event_run_id = event.get("run_id")
                        parents = event.get("parent_ids") or []
                        # Providers differ in the run_id hierarchy of custom events; walk from the
                        # event itself up through its ancestors to find the nearest tool call.
                        candidate_run_ids = [event_run_id, *reversed(parents)]
                        matched_step_id = None
                        for candidate in candidate_run_ids:
                            if candidate and str(candidate) in _tool_runs:
                                matched_step_id = _tool_runs[str(candidate)]
                                break
                        if not matched_step_id:
                            logger.warning("丢弃无法归属工具调用的 memory_stage: run_id=%s", event_run_id)
                            continue
                        matched_node = next(
                            (node for node in reversed(node_executions) if node.get("step_id") == matched_step_id),
                            None,
                        )
                        if not matched_node or matched_node.get("node_name") != "long_term_memory":
                            logger.warning("丢弃归属非长期记忆工具的 memory_stage: run_id=%s", event_run_id)
                            continue
                        stage_payload = {
                            "type": "memory_stage",
                            "step_id": matched_step_id,
                            "stage": raw_stage.get("stage"),
                            "status": raw_stage.get("status", "completed"),
                            "data": raw_stage.get("data") or {},
                        }
                        matched_node.setdefault("_memory_stages", []).append(stage_payload)
                        yield stage_payload
                    elif kind == "on_tool_end":
                        tool_output = event.get("data", {}).get("output")
                        tool_name = event.get("name", "unknown")
                        run_id = event.get("run_id")
                        matched_step_id = _tool_runs.pop(str(run_id), None) if run_id else None
                        matched_node = None
                        if matched_step_id:
                            matched_node = next(
                                (node for node in reversed(node_executions) if node.get("step_id") == matched_step_id),
                                None,
                            )
                            _tool_stack[:] = [
                                node for node in _tool_stack
                                if node.get("step_id") != matched_step_id
                            ]
                        elif _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        matched_step_id = matched_node.get("step_id") if matched_node else matched_step_id
                        if matched_node:
                            matched_node["status"] = "completed"
                            matched_node["output"] = self._truncate_data(tool_output)
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )
                            # Extract extra source info from the tool
                            self._extract_tool_sources(tool_name, matched_node)
                        trace.finish_tool(
                            step_id=matched_step_id,
                            tool_name=tool_name,
                            status="success",
                            output=tool_output,
                            meta=matched_node.get("meta") if matched_node else None,
                        )
                        logger.debug(f"工具调用结束: {tool_name}")
                        # Push the tool-end event in real time (with the updated meta)
                        yield {"type": "agent_log", "data": trace.to_dict()}
                        yield {"type": "tool_end", "step_id": matched_step_id, "name": tool_name, "output": self._truncate_data(tool_output), "meta": matched_node.get("meta") if matched_node else None}
                    elif kind == "on_tool_error":
                        error_msg = str(event.get("data", {}).get("error", ""))
                        tool_name = event.get("name", "unknown")
                        run_id = event.get("run_id")
                        matched_step_id = _tool_runs.pop(str(run_id), None) if run_id else None
                        matched_node = None
                        if matched_step_id:
                            matched_node = next(
                                (node for node in reversed(node_executions) if node.get("step_id") == matched_step_id),
                                None,
                            )
                            _tool_stack[:] = [
                                node for node in _tool_stack
                                if node.get("step_id") != matched_step_id
                            ]
                        elif _tool_stack:
                            matched_node = _tool_stack.pop()
                        else:
                            for n in reversed(node_executions):
                                if n["node_name"] == tool_name and n["status"] == "running":
                                    matched_node = n
                                    break
                        matched_step_id = matched_node.get("step_id") if matched_node else matched_step_id
                        if matched_node:
                            matched_node["status"] = "failed"
                            matched_node["error"] = error_msg[:500]
                            matched_node["elapsed_time"] = round(
                                (time.time() - matched_node.get("started_at", time.time())) * 1000, 2
                            )
                        # Push the tool-error event in real time
                        trace.finish_tool(
                            step_id=matched_step_id,
                            tool_name=tool_name,
                            status="error",
                            error=error_msg[:500],
                            meta=matched_node.get("meta") if matched_node else None,
                        )
                        yield {"type": "agent_log", "data": trace.to_dict()}
                        yield {"type": "tool_error", "step_id": matched_step_id, "name": tool_name, "error": error_msg[:500]}

                logger.debug(f"Agent 流式完成，共 {chunk_count} 个事件")
                # A step still running here lost its tool_end; settle it so the UI stops spinning
                for closing_event in self._close_running_tool_steps(
                    node_executions, trace, "工具调用未正常结束，未收到结果"
                ):
                    yield closing_event
                _tool_stack.clear()
                # Aggregate token usage
                output_messages = last_event.get("data", {}).get("output", {}).get("messages", [])
                stream_total_tokens = 0
                tokens_found = False
                for msg in reversed(output_messages):
                    if isinstance(msg, AIMessage):
                        stream_total_tokens = self._extract_tokens_from_message(msg)
                        logger.info(f"流式 token 统计: total_tokens={stream_total_tokens}")
                        tokens_found = True
                        break

                # Final-answer guard: a turn that used tools but ended on a procedural
                # sentence gets one extra tool-free closing turn (see
                # _needs_closing_answer). Failure of the closing turn is harmless — the
                # text produced so far is still delivered.
                if self._needs_closing_answer(full_content, node_executions):
                    logger.warning(
                        "Agent 以过程性语句收尾，追加一次收尾生成",
                        extra={
                            "content_length": len((full_content or '').strip()),
                            "tool_call_count": len(node_executions),
                        },
                    )
                    closing_stats: Dict[str, Any] = {"tokens": 0}
                    closing_full = ""
                    try:
                        async for piece in self._closing_answer_stream(
                            output_messages, trace, closing_stats
                        ):
                            full_content += piece
                            closing_full += piece
                            yield piece
                    except Exception as closing_exc:  # noqa: BLE001 - fall back to the text we have
                        logger.warning(
                            "收尾生成失败，保留原有正文",
                            extra={"error": str(closing_exc)[:300]},
                        )
                    # The closing turn IS the final answer (clean, no narration).
                    if closing_full.strip():
                        final_answer_text = closing_full.strip()
                    stream_total_tokens += int(closing_stats.get("tokens") or 0)
                    tokens_found = tokens_found or bool(closing_stats.get("tokens"))

                if tokens_found:
                    yield stream_total_tokens

                trace.finish_llm(output=full_content, tokens=stream_total_tokens)
                final_agent_log = trace.finalize(status="success", total_tokens=stream_total_tokens)
                yield {"type": "agent_log", "data": final_agent_log}
                yield {"type": "agent_log_final", "data": final_agent_log}

                # Yield node execution records (strip started_at and settle statuses)
                for node in node_executions:
                    node.pop("started_at", None)
                    if node.get("status") == "running":
                        node["status"] = "completed"
                if node_executions:
                    yield {"type": "node_executions", "data": node_executions}

                # Out-of-band final answer for consumers (the cluster supervisor's tool
                # result). Prefer the tracked last model message; fall back to the whole
                # streamed body so this event is never empty on a normal completion.
                # Opt-in only: non-cluster callers never receive this event.
                if emit_final_answer:
                    yield {
                        "type": "final_answer",
                        "content": final_answer_text.strip() or full_content.strip(),
                    }

            except GraphRecursionError:
                # The graph burned its step budget before a final answer. The run stays
                # a failure, but successful tool outputs are still in node_executions —
                # try one tool-free rescue turn that turns them into a real answer.
                # A failed turn carrying a real answer (rescued=True) is usable by the
                # cluster supervisor; a turn with no evidence degrades to the notice.
                abort_reason = (
                    f"执行在达到最大处理步骤限制（{self.max_iterations} 步）后仍未收尾"
                )
                logger.warning(
                    "Agent 流式执行达到步骤上限，尝试基于已有证据收尾",
                    extra={
                        "reason": "max_iterations",
                        "max_iterations": self.max_iterations,
                        "partial_content_length": len(full_content or ""),
                    },
                )
                # Mark unfinished tools as failed and tell the frontend, otherwise they spin forever
                for closing_event in self._close_running_tool_steps(
                    node_executions, trace, abort_reason
                ):
                    yield closing_event
                _tool_stack.clear()
                for node in node_executions:
                    node.pop("started_at", None)

                # Rescue: one tool-free turn over the evidence already collected.
                rescue_stats: Dict[str, Any] = {"tokens": 0}
                rescued_text = ""
                try:
                    async for piece in self._closing_answer_from_evidence_stream(
                        messages, node_executions, trace, rescue_stats
                    ):
                        rescued_text += piece
                        full_content = (full_content or "") + piece
                        yield piece
                except Exception as rescue_exc:  # noqa: BLE001 - rescue must not mask the abort
                    logger.warning(
                        "证据救援收尾失败，回落中止文案",
                        extra={"error": str(rescue_exc)[:300]},
                    )
                rescued = bool(rescued_text.strip())
                if not rescued:
                    # Rescue produced nothing: make sure the user sees an explicit
                    # "did not finish" notice, appended after any narration rather than
                    # leaving only a half-sentence like "让我继续搜索…".
                    if not (full_content or "").strip():
                        full_content = abort_reason
                        yield full_content
                    elif abort_reason not in full_content:
                        tail = f"\n\n{abort_reason}"
                        full_content += tail
                        yield tail
                # Attribute the rescue turn's tokens to the run (the normal cumulative
                # token yield is unreachable on this abort path).
                rescue_tokens = int(rescue_stats.get("tokens") or 0)
                if rescue_tokens:
                    yield rescue_tokens

                final_agent_log = trace.finalize(
                    status="error",
                    error=abort_reason,
                    total_tokens=rescue_tokens,
                )
                yield {"type": "agent_log", "data": final_agent_log}
                yield {"type": "agent_log_final", "data": final_agent_log}
                if node_executions:
                    yield {"type": "node_executions", "data": node_executions}
                # Out-of-band clean answer for consumers: on rescue it is the report
                # built from evidence (no intermediate narration); on a pure failure it
                # is the abort notice. The streamed full_content keeps the live trail.
                # Opt-in only: non-cluster callers never receive this event.
                if emit_final_answer:
                    yield {
                        "type": "final_answer",
                        "content": (rescued_text.strip() if rescued else abort_reason),
                    }
                # Failure marker (consumers turn it into status=failed). rescued=True
                # means the content already carries a usable answer built from evidence.
                yield {
                    "type": "run_error",
                    "code": "agent_aborted",
                    "reason": "max_iterations",
                    "error": abort_reason,
                    "rescued": rescued,
                }
            except Exception as e:
                # The caller converts this into a safe SSE-facing hint; the server keeps
                # the full exception for troubleshooting via model request_id and context.
                logger.error(f"Agent astream_events 失败: {str(e)}", exc_info=True)
                # Give every started tool step a terminal event before the error propagates
                for closing_event in self._close_running_tool_steps(
                    node_executions, trace, f"Agent 执行中断：{type(e).__name__}"
                ):
                    yield closing_event
                _tool_stack.clear()
                raise

        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"chat_stream 异常: {str(e)}")
            logger.error("=" * 80, exc_info=True)
            raise
        finally:
            _tool_runs.clear()
            logger.info("=" * 80)
            logger.info("chat_stream 方法执行结束")
            logger.info("=" * 80)
