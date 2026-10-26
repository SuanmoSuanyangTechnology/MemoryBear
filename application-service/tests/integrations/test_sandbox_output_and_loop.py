"""Tests for sandbox output cleaning and the tool-call loop abort."""

from __future__ import annotations

import sys
import types

import pytest

try:  # pragma: no cover - environment dependent
    import datrie  # noqa: F401
except ImportError:  # pragma: no cover
    _stub = types.ModuleType("datrie")
    _stub.Trie = lambda *a, **k: {}
    sys.modules["datrie"] = _stub

from src.tools.builtin.sandbox_output import clean_terminal_output


def test_clean_output_strips_ansi_and_keeps_text():
    raw = "\x1b[31mERROR: no matching distribution\x1b[0m\n\x1b[1m[notice]\x1b[0m done"
    assert clean_terminal_output(raw) == "ERROR: no matching distribution\n[notice] done"


def test_clean_output_collapses_progress_bar_redraws():
    raw = (
        "Downloading x\n"
        "\x1b[2K   0.0/7.1 MB\r\x1b[2K   3.5/7.1 MB\r\x1b[2K   7.1/7.1 MB\n"
        "Network is unreachable\n"
    )
    out = clean_terminal_output(raw)
    assert out == "Downloading x\n   7.1/7.1 MB\nNetwork is unreachable\n"
    assert "0.0/7.1" not in out


def test_clean_output_handles_empty_and_crlf():
    assert clean_terminal_output(None) == ""
    assert clean_terminal_output("") == ""
    assert clean_terminal_output("a\r\nb") == "a\nb"


def _bare_agent():
    from src.agent.langchain_agent import LangChainAgent

    agent = LangChainAgent.__new__(LangChainAgent)  # skip __init__: no LLM needed
    agent._loop_abort_reason = None
    return agent


def test_over_limit_returns_text_within_grace_then_aborts():
    from src.agent.langchain_agent import (
        TOOL_LIMIT_GRACE_REJECTIONS,
        ToolCallLoopError,
    )
    from langgraph.errors import GraphRecursionError

    agent = _bare_agent()
    limit = 10

    # rejections 1..GRACE only return a message; the model still gets a chance to stop
    for n in range(1, TOOL_LIMIT_GRACE_REJECTIONS + 1):
        reply = agent._over_limit_reply("terminal_tool", limit, limit + n)
        assert "请勿再调用" in reply
        assert agent._loop_abort_reason is None

    # the next one aborts the loop, through the existing recursion-limit handling
    with pytest.raises(ToolCallLoopError) as info:
        agent._over_limit_reply("terminal_tool", limit, limit + TOOL_LIMIT_GRACE_REJECTIONS + 1)
    assert isinstance(info.value, GraphRecursionError)
    assert "terminal_tool" in agent._loop_abort_reason
