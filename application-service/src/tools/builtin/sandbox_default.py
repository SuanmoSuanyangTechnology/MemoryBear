"""沙箱工具的默认装配 - 开关打开后，每个 Agent 每次运行自动带上全部沙箱工具。

不再有「内置工具」里的沙箱条目，Agent 配置里也不用勾选：
- 开关是 ``ENABLE_SANDBOX_SERVICE``（沙箱集成运行时已启动即视为开启）。
- 每次装配都新建一组工具实例（不入库、不缓存），运行时上下文（user_id / workspace_id /
  conversation_id）由调用方注入，实例之间互不影响。
- 入口：``AgentRunService.load_tools_config``（草稿运行 / 应用对话 / 多 Agent 子 Agent 共用）
  和工作流 Agent 节点的 ``_load_tools_async``。

成员（模型看到的工具名固定）：
``code_interpreter_tool`` / ``terminal_tool`` / ``sandbox_read_file`` /
``sandbox_write_file`` / ``sandbox_publish_file``。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List

from .base import BuiltinTool
from .code_interpreter_tool import CodeInterpreterTool
from .sandbox_file_tools import (
    SandboxPublishFileTool,
    SandboxReadFileTool,
    SandboxWriteFileTool,
)
from .terminal_tool import TerminalTool

logger = logging.getLogger(__name__)

# 展示/注入顺序：先执行，再读写文件，最后带出产物
SANDBOX_TOOL_CLASSES: tuple[type[BuiltinTool], ...] = (
    CodeInterpreterTool,
    TerminalTool,
    SandboxReadFileTool,
    SandboxWriteFileTool,
    SandboxPublishFileTool,
)

SANDBOX_TOOL_NAMES: frozenset[str] = frozenset({
    "code_interpreter_tool",
    "terminal_tool",
    "sandbox_read_file",
    "sandbox_write_file",
    "sandbox_publish_file",
})


def is_sandbox_enabled() -> bool:
    from ...integrations.sandbox.runtime import is_sandbox_integration_enabled

    return is_sandbox_integration_enabled()


def build_sandbox_tools(
    *,
    user_id: Any = None,
    workspace_id: Any = None,
    conversation_id: Any = None,
) -> List[Any]:
    """返回全部沙箱工具对应的 LangChain 工具；开关关闭时返回空列表。

    user_id / workspace_id / conversation_id 为空的项不写入上下文，之后调用方
    还可以对 ``tool.tool_instance`` 再调 ``set_runtime_context`` 补充（如 conversation_id）。
    """
    if not is_sandbox_enabled():
        return []
    tools: List[Any] = []
    for cls in SANDBOX_TOOL_CLASSES:
        instance = cls(f"sandbox-default:{cls.__name__}", {})
        # 与 OperationTool 一致：draft_run_service 序列化工具时靠它记录 operation
        instance.operation = instance.name
        instance.set_runtime_context(
            user_id=str(user_id) if user_id is not None else None,
            workspace_id=str(workspace_id) if workspace_id is not None else None,
            conversation_id=str(conversation_id) if conversation_id is not None else None,
        )
        tools.append(instance.to_langchain_tool())
    return tools


def drop_sandbox_tools(tools: Iterable[Any]) -> List[Any]:
    """去掉列表里已有的沙箱工具（按工具名），避免旧配置残留与默认装配重名。"""
    return [t for t in tools if getattr(t, "name", None) not in SANDBOX_TOOL_NAMES]
