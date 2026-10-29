"""Regression tests for "a sub-agent that did not answer must not look successful".

Observed failure (cluster supervisor_loop, 2026-10-28): a research sub-agent spent its
per-tool call budget while the searches it needed were still succeeding, and an
over-limit rejection raised inside the tool, killing the graph mid-sentence. The
narration ("我将为您搜索……") was then recorded as a usable result and the supervisor
re-dispatched the same task twice.

The current contract:

1. An over-limit tool call is an IN-BAND message, never an exception — the model answers
   from what it already has.
2. The graph step budget is sized to cover a run that spends its whole tool budget, with
   slack for the final answer.
3. Only a genuine step-budget abort produces ``run_error``; when usable tool results
   were collected, one tool-free "rescue" turn turns them into a real answer
   (``rescued=True``). The turn is still recorded failed, but a supervisor adopts it.
4. The dead ``news`` search branch is gone.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration
from langgraph.errors import GraphRecursionError

from src.agent.langchain_agent import (
    CLOSING_ANSWER_INSTRUCTION,
    CLOSING_ANSWER_MIN_CHARS,
    RECURSION_LIMIT_SAFETY_SLACK,
    LangChainAgent,
)

# --------------------------------------------------------------------------- helpers


def _bare_agent(**overrides) -> LangChainAgent:
    """A LangChainAgent with __init__ skipped: no model, no graph, no DB."""
    agent = LangChainAgent.__new__(LangChainAgent)
    defaults = dict(
        tools=[],
        provider="openai",
        model_name="fake-model",
        strategy="react",
        system_prompt="SP",
        deep_thinking=False,
        max_iterations=10,
        _tool_meta_map={},
        _tool_call_counts={},
        streaming=True,
    )
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(agent, key, value)
    return agent


class _AbortingAgent:
    """Stands in for the LangGraph agent: raises on the first event."""

    def __init__(self, error: BaseException | None = None):
        self._error = error or GraphRecursionError("Reached the step budget.")

    async def astream_events(self, *_args, **_kwargs):
        raise self._error
        yield  # pragma: no cover - makes this an async generator


class _EvidenceThenAbortAgent:
    """Streams one successful tool round, then dies on the step budget."""

    EVIDENCE = "上证指数跌1.33%报3648点，成交1.47万亿；Meta资本开支下限1350亿美元。"

    def __init__(self, *, narration: str = "让我先搜索，再继续补充财报数据："):
        self.narration = narration

    async def astream_events(self, *_args, **_kwargs):
        yield {"event": "on_chat_model_start", "name": "model", "data": {}, "run_id": "r0"}
        yield {"event": "on_chat_model_stream", "name": "model",
               "data": {"chunk": AIMessageChunk(content=self.narration)}, "run_id": "r0"}
        yield {"event": "on_tool_start", "name": "baidu_search_tool",
               "data": {"input": {"query": "q"}}, "run_id": "t1"}
        yield {"event": "on_tool_end", "name": "baidu_search_tool",
               "data": {"output": self.EVIDENCE}, "run_id": "t1"}
        raise GraphRecursionError("Reached the step budget.")
        yield  # pragma: no cover


class _NarratingAgent:
    """Streams one narration chunk after a single tool round, then stops normally."""

    def __init__(self, narration: str):
        self._narration = narration

    async def astream_events(self, *_args, **_kwargs):
        yield {"event": "on_chat_model_start", "name": "model", "data": {}, "run_id": "r0"}
        yield {"event": "on_chat_model_stream", "name": "model",
               "data": {"chunk": AIMessageChunk(content=self._narration)}, "run_id": "r0"}
        yield {"event": "on_tool_start", "name": "baidu_search_tool",
               "data": {"input": {"query": "q"}}, "run_id": "t1"}
        yield {"event": "on_tool_end", "name": "baidu_search_tool",
               "data": {"output": "[]"}, "run_id": "t1"}
        yield {"event": "on_chat_model_start", "name": "model", "data": {}, "run_id": "r1"}
        yield {"event": "on_chat_model_start", "name": "model", "data": {}, "run_id": "r2"}


class _MultiRoundAgent:
    """Round 1 streams narration and issues a tool call; round 2 gives the real answer."""

    NARRATION = "我将为您搜索最新的A股数据。让我先进行多角度搜索。"
    FINAL = (
        "最终报告：10月27日A股三大指数集体下挫，上证指数跌1.33%报3648.30点，"
        "深证成指跌2.5%报11245.73点，创业板指跌3.41%报2548.15点，"
        "全天成交约1.47万亿元、较前一日缩量约1500亿元，全市场超4900只个股收跌，"
        "仅银行板块逆势上涨；隔夜美股方面，科技股受资本开支预期与AI估值担忧拖累，"
        "Meta三季度营收创新高但自由现金流多年来首次转负、小幅上调全年资本开支下限，"
        "特斯拉三季度营收280.46亿美元同比基本持平，二者财报后股价均明显波动。"
        "关键宏观因素为油价上行加剧通胀担忧、市场对AI巨额资本开支可持续性产生分歧。"
        "以上数据与结论均来自本轮百度检索到的新闻稿与分析报告摘要。"
    )

    def _end(self, run_id: str, message: AIMessage):
        return {"event": "on_chat_model_end", "name": "model",
                "data": {"output": ChatGeneration(message=message)}, "run_id": run_id}

    async def astream_events(self, *_args, **_kwargs):
        from langchain_core.messages import ToolMessage

        yield {"event": "on_chat_model_start", "name": "model", "data": {}, "run_id": "r0"}
        yield {"event": "on_chat_model_stream", "name": "model",
               "data": {"chunk": AIMessageChunk(content=self.NARRATION)}, "run_id": "r0"}
        tool_call = {"name": "baidu_search_tool", "args": {"query": "q"}, "id": "c1"}
        yield self._end("r0", AIMessage(content=self.NARRATION, tool_calls=[tool_call]))
        yield {"event": "on_tool_start", "name": "baidu_search_tool",
               "data": {"input": {"query": "q"}}, "run_id": "t1"}
        yield {"event": "on_tool_end", "name": "baidu_search_tool",
               "data": {"output": "上证指数跌1.33%，成交1.47万亿。"}, "run_id": "t1"}
        yield {"event": "on_chat_model_start", "name": "model", "data": {}, "run_id": "r1"}
        yield {"event": "on_chat_model_stream", "name": "model",
               "data": {"chunk": AIMessageChunk(content=self.FINAL)}, "run_id": "r1"}
        yield self._end("r1", AIMessage(content=self.FINAL))
        # The graph's terminal event: its output carries the full message list. This is
        # the last_event the engine reads the final AIMessage / tokens from.
        yield {"event": "on_chain_end", "name": "agent", "data": {"output": {"messages": [
            AIMessage(content=self.NARRATION, tool_calls=[tool_call]),
            ToolMessage(content="上证指数跌1.33%，成交1.47万亿。", tool_call_id="c1"),
            AIMessage(content=self.FINAL),
        ]}}}


class _FakeLLM:
    def __init__(self, text: str, *, fail: bool = False):
        self._text = text
        self._fail = fail
        self.prompts: list = []

    def _record(self, prompt):
        self.prompts.append(prompt)
        if self._fail:
            raise RuntimeError("closing turn unavailable")

    async def astream(self, prompt):
        self._record(prompt)
        for piece in (self._text[:4], self._text[4:]):
            if piece:
                yield AIMessageChunk(content=piece)

    async def ainvoke(self, prompt):
        self._record(prompt)
        from langchain_core.messages import AIMessage

        return AIMessage(content=self._text)


class _ExplodingLLM:
    """Fails loudly if a rescue turn is attempted without any evidence."""

    async def astream(self, prompt):
        raise AssertionError("rescue LLM must not be called when there is no evidence")
        yield  # pragma: no cover

    async def ainvoke(self, prompt):
        raise AssertionError("rescue LLM must not be called when there is no evidence")


async def _drain(agen) -> list:
    return [chunk async for chunk in agen]


# ------------------------------------------------ 1: over-limit never aborts the run


def test_over_limit_reply_is_always_in_band_and_forces_an_answer():
    agent = _bare_agent()
    limit = 5
    # No matter how many times the model ignores the hint, the tool returns a message
    # instead of raising: the graph keeps running and the model gets to answer.
    for count in range(limit + 1, limit + 8):
        reply = agent._over_limit_reply("baidu_search_tool", limit, count)
        assert isinstance(reply, str)
        assert "达到上限" in reply
        assert "最终答案" in reply
        assert "不要重试" in reply


def test_over_limit_wrapper_returns_text_without_executing_or_raising():
    import asyncio

    agent = _bare_agent(tools=[])
    calls = []

    class _Tool:
        name = "baidu_search_tool"

        def _run(self, **_kw):
            return "real result"

        async def _arun(self, **_kw):
            calls.append("ran")
            return "real result"

    tool = _Tool()
    agent.tools = [tool]
    agent._wrap_tools_with_call_limit(1)

    async def go():
        return await tool._arun(query="a"), await tool._arun(query="b")

    first, second = asyncio.run(go())
    assert first == "real result"
    assert isinstance(second, str) and "达到上限" in second
    assert calls == ["ran"]  # the rejected call never executed the real tool


# ------------------------------------------------ 2: step budget covers a full run


def test_recursion_limit_covers_every_tool_call_plus_the_answer():
    # The observed run used one search tool and died around super-step 17 under the old
    # formula (5 + tools * limit * 2 = 15 with limit 5). The new formula leaves headroom.
    limit = 5
    assert LangChainAgent._compute_recursion_limit(1, limit) >= 2 * limit + 4
    assert LangChainAgent._compute_recursion_limit(1, limit) > 17
    # No tools: opening + closing LLM and the slack, still positive.
    assert LangChainAgent._compute_recursion_limit(0, limit) == 2 + RECURSION_LIMIT_SAFETY_SLACK
    # Monotonic in both dimensions.
    assert (
        LangChainAgent._compute_recursion_limit(3, limit)
        > LangChainAgent._compute_recursion_limit(1, limit)
    )
    assert (
        LangChainAgent._compute_recursion_limit(1, 8)
        > LangChainAgent._compute_recursion_limit(1, 5)
    )


# --------------------------------- 3: step-budget abort -> run_error, rescued when possible


@pytest.mark.asyncio
async def test_stream_abort_without_evidence_emits_failed_run_error_and_notice():
    agent = _bare_agent()
    agent.agent = _AbortingAgent()
    agent._underlying_llm = _ExplodingLLM()

    events = await _drain(agent.chat_stream(message="今天A股怎么样"))

    markers = [e for e in events if isinstance(e, dict) and e.get("type") == "run_error"]
    assert len(markers) == 1, events
    assert markers[0]["code"] == "agent_aborted"
    assert markers[0]["reason"] == "max_iterations"
    assert markers[0]["rescued"] is False
    # The user still sees the abort notice rather than an empty bubble.
    text = "".join(e for e in events if isinstance(e, str))
    assert "最大处理步骤限制" in text


@pytest.mark.asyncio
async def test_stream_abort_with_evidence_is_rescued_into_a_report():
    report = "结论：A股10月27日走弱，上证跌1.33%，成交1.47万亿；美股科技股受资本开支预期拖累。"
    agent = _bare_agent()
    agent.agent = _EvidenceThenAbortAgent()
    agent._underlying_llm = _FakeLLM(report)

    events = await _drain(agent.chat_stream(message="今天市场怎么样"))

    markers = [e for e in events if isinstance(e, dict) and e.get("type") == "run_error"]
    assert len(markers) == 1
    assert markers[0]["rescued"] is True
    assert markers[0]["reason"] == "max_iterations"

    text = "".join(e for e in events if isinstance(e, str))
    assert report in text  # the rescued answer
    # The rescue prompt was built from the successful tool result, not from thin air.
    prompt_blob = "\n".join(
        str(getattr(m, "content", m)) for m in agent._underlying_llm.prompts[0]
    )
    assert _EvidenceThenAbortAgent.EVIDENCE in prompt_blob
    assert "不会再有任何新信息" in prompt_blob


@pytest.mark.asyncio
async def test_failed_rescue_turn_falls_back_to_abort_notice():
    agent = _bare_agent()
    agent.agent = _EvidenceThenAbortAgent()
    agent._underlying_llm = _FakeLLM("", fail=True)

    events = await _drain(agent.chat_stream(message="q"))

    markers = [e for e in events if isinstance(e, dict) and e.get("type") == "run_error"]
    assert markers and markers[0]["rescued"] is False
    text = "".join(e for e in events if isinstance(e, str))
    assert "最大处理步骤限制" in text


@pytest.mark.asyncio
async def test_non_streaming_abort_with_evidence_is_rescued_and_marked_failed():
    report = "结论（非流式）：上证跌1.33%，成交1.47万亿。"
    agent = _bare_agent()
    agent.agent = _EvidenceThenAbortAgent(narration="我将为您搜索。")
    agent._underlying_llm = _FakeLLM(report)

    result = await agent.chat(message="今天市场怎么样")

    assert result["content"] == report  # non-streaming replaces, not appends
    assert result["run_error"]["reason"] == "max_iterations"
    assert result["run_error"]["rescued"] is True
    prompt_blob = "\n".join(
        str(getattr(m, "content", m)) for m in agent._underlying_llm.prompts[0]
    )
    assert _EvidenceThenAbortAgent.EVIDENCE in prompt_blob


def test_evidence_digest_skips_failures_rejections_and_running_steps():
    agent = _bare_agent()
    digest = agent._evidence_digest([
        {"status": "completed", "node_name": "baidu_search_tool",
         "output": "上证指数下跌，成交1.47万亿。"},
        {"status": "completed", "node_name": "baidu_search_tool",
         "output": "[工具调用限制] 工具 'baidu_search_tool' 在本轮已调用 5 次，达到上限"},
        {"status": "completed", "node_name": "baidu_search_tool",
         "output": "content='{\"success\": false, \"error\": \"decode failed\"}'"},
        {"status": "failed", "node_name": "baidu_search_tool", "output": None,
         "error": "aborted"},
    ])
    assert "上证指数下跌" in digest
    assert "[工具调用限制]" not in digest
    assert "decode failed" not in digest


# ---------------------------------------- B (unchanged): narration gets a closing turn


def test_closing_answer_needed_only_after_tools_and_only_for_narration():
    agent = _bare_agent()
    tools_used = [{"node_name": "baidu_search_tool"}]
    narration = "我将为您搜索最新的A股数据。让我先进行多角度搜索。"

    assert agent._needs_closing_answer(narration, tools_used) is True
    assert agent._needs_closing_answer("", tools_used) is True
    assert agent._needs_closing_answer("现在让我继续搜索关键新闻：", tools_used) is True
    assert agent._needs_closing_answer(narration, []) is False
    long_answer = "结论如下。" + "上证指数上涨 0.8%，成交额 1.2 万亿。" * 30
    assert len(long_answer) > CLOSING_ANSWER_MIN_CHARS
    assert agent._needs_closing_answer(long_answer, tools_used) is False


def test_closing_turn_prompt_drops_empty_tool_call_message_and_forbids_tools():
    agent = _bare_agent()
    from langchain_core.messages import AIMessage, HumanMessage

    messages = [
        HumanMessage(content="今天A股怎么样"),
        AIMessage(content=""),
    ]
    prompt = agent._closing_answer_messages(messages)

    assert CLOSING_ANSWER_INSTRUCTION.strip() in prompt[0].content
    assert "不要再调用任何工具" in prompt[0].content
    assert [type(m).__name__ for m in prompt[1:]] == ["HumanMessage", "HumanMessage"]


@pytest.mark.asyncio
async def test_normal_narration_run_is_completed_by_a_closing_turn():
    agent = _bare_agent()
    narration = "我将为您搜索最新的A股数据。让我先进行多角度搜索。"
    agent.agent = _NarratingAgent(narration)
    agent._underlying_llm = _FakeLLM("结论：上证指数上涨 0.8%，成交额 1.2 万亿。")

    events = await _drain(agent.chat_stream(message="今天A股怎么样"))

    text = "".join(e for e in events if isinstance(e, str))
    assert narration in text and "结论：上证指数上涨" in text
    assert len(agent._underlying_llm.prompts) == 1
    assert not [e for e in events if isinstance(e, dict) and e.get("type") == "run_error"]


@pytest.mark.asyncio
async def test_stream_emits_clean_final_answer_only_when_opted_in():
    # The live stream carries both the intermediate narration and the report, but the
    # out-of-band final_answer must be ONLY the last tool-call-free model message — and
    # it exists ONLY when the cluster sub-agent path opts in (emit_final_answer=True).
    async def run(opt_in: bool):
        agent = _bare_agent()
        agent.agent = _MultiRoundAgent()
        agent._underlying_llm = _FakeLLM("（不应触发额外收尾）")
        events = await _drain(
            agent.chat_stream(message="今天市场怎么样", emit_final_answer=opt_in)
        )
        return agent, events

    # Opted in (cluster sub-agent): exactly one clean final_answer, no narration.
    _agent_on, events_on = await run(True)
    final = [e for e in events_on if isinstance(e, dict) and e.get("type") == "final_answer"]
    assert len(final) == 1
    assert final[0]["content"] == _MultiRoundAgent.FINAL
    assert _MultiRoundAgent.NARRATION not in final[0]["content"]
    assert not _agent_on._underlying_llm.prompts  # no rescue/closing turn
    streamed = "".join(e for e in events_on if isinstance(e, str))
    assert _MultiRoundAgent.NARRATION in streamed and _MultiRoundAgent.FINAL in streamed

    # Default (single-Agent app / workflow node / supervisor): the event must NOT exist,
    # so a passthrough consumer cannot leak it to the frontend.
    _agent_off, events_off = await run(False)
    assert not [e for e in events_off if isinstance(e, dict) and e.get("type") == "final_answer"]
    streamed_off = "".join(e for e in events_off if isinstance(e, str))
    assert _MultiRoundAgent.FINAL in streamed_off  # body still streams normally


# --------------------------------------- orchestrator feeds the supervisor only the answer


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@pytest.mark.asyncio
async def test_orchestrator_returns_clean_output_not_streamed_narration():
    import types

    from src.services.multi_agent_orchestrator import SubAgentTool

    narration = _MultiRoundAgent.NARRATION
    report = _MultiRoundAgent.FINAL
    frames = [
        _sse("message", {"content": narration}),
        _sse("message", {"content": report}),
        _sse("agent_complete", {"status": "completed", "output": report}),
    ]

    class _Orch:
        sub_agents = {"a": {"config": {}}}
        _cluster_variables = types.SimpleNamespace(values={})
        current_conversation_id = None
        current_execution_id = None
        _normalized_mode = "supervisor_loop"
        _loop_user_id = None
        _loop_web_search = False
        _loop_memory = True
        _loop_storage_type = ""
        _loop_user_rag_memory_id = ""

        async def _execute_sub_agent_stream(self, *_a):
            for frame in frames:
                yield frame

        def _inject_agent_meta(self, event, _meta):
            return event

        def _strip_conversation_id(self, event):
            return event

        def _sub_event_meta(self, *_a):
            return {}

    tool = SubAgentTool.__new__(SubAgentTool)
    tool._orchestrator = _Orch()
    tool.agent_id = "a"
    tool.agent_name = "调研agent"
    tool.description = "d"
    tool.event_sink = None
    tool.calls = 0

    result = await tool._run_dispatch(tool._orchestrator, {"config": {}}, "调研今日市场")

    # Success path: exactly the clean agent_complete.output, no narration, no wrapper.
    assert result == report
    assert narration not in result


@pytest.mark.asyncio
async def test_orchestrator_rescued_return_uses_clean_output_without_narration():
    import types

    from src.services.multi_agent_orchestrator import SubAgentTool

    narration = "让我继续搜索财报数据："
    report = "结论（救援）：上证跌1.33%，成交1.47万亿，科技股受资本开支预期拖累。"
    frames = [
        _sse("message", {"content": narration}),
        _sse("message", {"content": report}),
        _sse("agent_complete", {"status": "failed", "rescued": True, "output": report}),
    ]

    class _Orch:
        sub_agents = {"a": {"config": {}}}
        _cluster_variables = types.SimpleNamespace(values={})
        current_conversation_id = None
        current_execution_id = None
        _normalized_mode = "supervisor_loop"
        _loop_user_id = None
        _loop_web_search = False
        _loop_memory = True
        _loop_storage_type = ""
        _loop_user_rag_memory_id = ""

        async def _execute_sub_agent_stream(self, *_a):
            for frame in frames:
                yield frame

        def _inject_agent_meta(self, event, _meta):
            return event

        def _strip_conversation_id(self, event):
            return event

        def _sub_event_meta(self, *_a):
            return {}

    tool = SubAgentTool.__new__(SubAgentTool)
    tool._orchestrator = _Orch()
    tool.agent_id = "a"
    tool.agent_name = "调研agent"
    tool.description = "d"
    tool.event_sink = None
    tool.calls = 0

    result = await tool._run_dispatch(tool._orchestrator, {"config": {}}, "调研")

    assert report in result
    assert narration not in result
    assert "可直接采用" in result


# ------------------------------------------------------------- 4: news branch is gone


def test_baidu_search_news_branch_is_removed():
    import asyncio

    from src.tools.builtin.baidu_search_tool import BaiduSearchTool

    tool = BaiduSearchTool.__new__(BaiduSearchTool)
    enum_values: list = []
    for p in tool.parameters:
        if p.name == "search_type":
            enum_values = list(p.enum or [])
    assert "news" not in enum_values
    assert not hasattr(BaiduSearchTool, "_news_search")

    result = asyncio.run(tool.execute(search_type="news", query="科技股"))
    # A legacy caller gets an actionable, non-retryable error, not a decode exception.
    assert result.success is False
    assert result.error_code == "BAIDU_SEARCH_ERROR"
    assert "web" in result.error and "不要重试" in result.error


# --------------------------------------------- supervisor classifies the agent_complete


def _complete(payload: dict) -> str:
    return "event: agent_complete\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def test_supervisor_classifies_completion_failure_and_rescue():
    from src.services.multi_agent_orchestrator import SubAgentTool

    ok = _complete({"status": "completed", "output": "完整报告"})
    assert SubAgentTool._sub_outcome_from_event(ok) == (None, False)

    failed = _complete({
        "status": "failed",
        "output": "我将为您搜索……",
        "error": {"message": "执行在达到最大处理步骤限制后仍未收尾"},
    })
    reason, rescued = SubAgentTool._sub_outcome_from_event(failed)
    assert rescued is False and "最大处理步骤限制" in reason

    rescued_top = _complete({"status": "failed", "rescued": True, "output": "真实报告"})
    assert SubAgentTool._sub_outcome_from_event(rescued_top) == (None, True)

    rescued_nested = _complete({
        "status": "failed",
        "rescued": True,
        "error": {"message": "x", "rescued": True},
    })
    assert SubAgentTool._sub_outcome_from_event(rescued_nested) == (None, True)

    assert SubAgentTool._sub_outcome_from_event("not an event") == (None, False)