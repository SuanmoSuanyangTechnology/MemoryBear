"""代码解释器内置工具 - 在独立沙箱服务（sandbox-service）里执行 Python 代码

只支持 Python：沙箱模板里的代码内核（Cube 数据面 ``POST /contexts``）只接受 python，
传 nodejs 会得到 400。Node.js 脚本请用终端工具执行（``node x.js``）。

定位说明
--------
- 代码不在主应用进程里跑，而是通过 ``integrations.sandbox`` 调用独立的
  sandbox-service；沙箱是按 (tenant_id, workspace_id, end_users.id) 隔离的微虚机，
  同一会话内多次调用共享内核（变量保留），同一终端用户的 ``/workspace`` 文件跨会话保留。
- 运行时上下文由 chat 服务注入：``user_id`` 必须是 ``end_users.id``，``conversation_id``
  是当前会话 ID，``workspace_id`` 用来反查租户。缺少任何一项都会拒绝执行，
  而不是退化成共用一个沙箱。
- 沙箱服务未启用（``ENABLE_SANDBOX_SERVICE=false``）时返回明确的配置错误。
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from ...schemas.tool_schema import ParameterType, ToolParameter, ToolResult
from .base import BuiltinTool
from .sandbox_output import sanitize_sandbox_output

logger = logging.getLogger(__name__)

_LANGUAGE = "python3"
_ANONYMOUS_USER_IDS = {"", "anonymous"}

# SyntaxError messages that mean the source stopped mid-statement (the model's output hit
# the token limit), not that the user wrote bad code. Sending these to the sandbox spends
# a round trip only to get the same SyntaxError back.
_TRUNCATED_SYNTAX_MARKERS = (
    "incomplete input",
    "unexpected EOF",
    "was never closed",
    "unterminated",
    "EOF while scanning",
)


def _truncated_code_reason(code: str) -> Optional[str]:
    """Return the SyntaxError text when ``code`` is cut off mid-statement, else None.

    Compiled locally and cheaply: a truncation is a property of the source text, so
    there is nothing the sandbox can add. Any other syntax error is left to the sandbox
    so the message the model reads stays the real interpreter's.
    """

    try:
        compile(code, "<code_interpreter_tool>", "exec")
    except SyntaxError as exc:
        message = str(exc.msg or exc)
        if any(marker.lower() in message.lower() for marker in _TRUNCATED_SYNTAX_MARKERS):
            return message
    except (ValueError, TypeError):
        # NUL bytes and the like: let the sandbox decide how to report them.
        return None
    return None


class CodeInterpreterTool(BuiltinTool):
    """代码解释器 - 在隔离沙箱中执行代码并返回 stdout / stderr"""

    @property
    def name(self) -> str:
        return "code_interpreter_tool"

    @property
    def description(self) -> str:
        return (
            "代码解释器：在一台隔离的 Linux 沙箱中执行 Python3 代码，返回标准输出和错误输出。"
            "只支持 Python；需要运行 Node.js 或其他命令行程序时请改用终端工具。"
            "同一会话内变量会保留（持久内核）；需要长期保留或交给用户的文件请写到 /workspace 目录，"
            "同一终端用户的后续会话仍可读取。适合数据处理与计算、生成和转换文件（如 PPT/Excel/PDF/图片）等场景。"
        )

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="code",
                type=ParameterType.STRING,
                description="要执行的 Python3 代码",
                required=True,
            ),
            ToolParameter(
                name="timeout",
                type=ParameterType.NUMBER,
                description="执行超时（秒），不能超过沙箱服务的上限",
                required=False,
                minimum=1,
                maximum=300,
            ),
        ]

    def resolve_execution_timeout(self, parameters: dict) -> float:
        """代码自身的 timeout 参数 + 30 秒余量；未传时按沙箱服务读超时 330s 再留余量。"""
        try:
            requested = float(parameters.get("timeout"))
        except (TypeError, ValueError):
            return 360.0
        return max(requested, 1.0) + 30.0

    async def execute(self, **kwargs) -> ToolResult:
        start_time = time.time()

        def _elapsed() -> float:
            return time.time() - start_time

        from ...integrations.sandbox.client import SandboxPrincipal
        from ...integrations.sandbox.errors import (
            SandboxClientError,
            SandboxConfigurationError,
            SandboxServiceError,
        )
        from ...integrations.sandbox.runtime import get_sandbox_service_client

        code = kwargs.get("code")
        language = kwargs.get("language") or _LANGUAGE
        timeout = kwargs.get("timeout")

        # 旧的 Agent 配置或模型可能仍传 language=nodejs：明确拒绝并指路，而不是当 Python 执行
        if str(language).lower() not in (_LANGUAGE, "python"):
            return ToolResult.error_result(
                error="代码解释器只支持 Python3；运行 Node.js 请用终端工具执行，如 node script.js",
                error_code="INVALID_ARGUMENT",
                execution_time=_elapsed(),
            )

        # 代码在上游被长度上限截断时，执行只会拿回同一个 SyntaxError。提前拒掉，
        # 并告诉模型怎么分次写入，而不是让它白白多跑一轮。
        if isinstance(code, str) and (truncated_reason := _truncated_code_reason(code)):
            logger.warning("sandbox_code_truncated reason=%s", truncated_reason)
            return ToolResult.error_result(
                error=(
                    f"代码不完整，本次未执行（{truncated_reason}）。"
                    "通常是输出达到长度上限被截断，引号、括号或缩进块没有闭合。"
                    "请把代码拆成多次较短的执行，或先用 sandbox_write_file 的 path + content "
                    "把长代码写成脚本文件，再用终端 python 文件名 执行。"
                ),
                error_code="SANDBOX_CODE_TRUNCATED",
                execution_time=_elapsed(),
            )

        user_id = str(self.get_runtime_context("user_id") or "")
        workspace_id = str(self.get_runtime_context("workspace_id") or "")
        conversation_id = str(self.get_runtime_context("conversation_id") or "")
        if user_id in _ANONYMOUS_USER_IDS or not workspace_id or not conversation_id:
            return ToolResult.error_result(
                error="代码解释器需要在有终端用户、工作空间和会话的对话中使用",
                error_code="SANDBOX_CONTEXT_MISSING",
                execution_time=_elapsed(),
            )

        try:
            client = get_sandbox_service_client()
        except SandboxConfigurationError as exc:
            return ToolResult.error_result(
                error=str(exc), error_code="SANDBOX_NOT_ENABLED", execution_time=_elapsed()
            )

        tenant_id = await self._resolve_tenant_id(workspace_id)
        if not tenant_id:
            return ToolResult.error_result(
                error="无法确定工作空间所属租户",
                error_code="SANDBOX_TENANT_NOT_FOUND",
                execution_time=_elapsed(),
            )

        principal = SandboxPrincipal(
            tenant_id=tenant_id, workspace_id=workspace_id, end_user_id=user_id
        )
        try:
            result = await client.execute(
                principal,
                conversation_id=conversation_id,
                code=str(code),
                language=_LANGUAGE,
                timeout_s=float(timeout) if timeout not in (None, "") else None,
            )
        except SandboxServiceError as exc:
            logger.warning(
                "sandbox_execute_refused kind=%s retryable=%s", exc.kind, exc.retryable
            )
            hint = "，请稍后重试" if exc.retryable else ""
            return ToolResult.error_result(
                error=f"沙箱服务拒绝了请求（{exc.kind}）{hint}",
                error_code=f"SANDBOX_{exc.kind.upper()}",
                execution_time=_elapsed(),
                metadata={"retryable": exc.retryable, "retry_after_s": exc.retry_after_s},
            )
        except SandboxClientError as exc:
            logger.warning("sandbox_execute_failed error=%s", type(exc).__name__)
            return ToolResult.error_result(
                error=f"沙箱服务不可用：{exc}",
                error_code="SANDBOX_UNAVAILABLE",
                execution_time=_elapsed(),
            )

        data: dict[str, Any] = {
            "status": result.status,
            "stdout": sanitize_sandbox_output(result.stdout),
            "stderr": sanitize_sandbox_output(result.stderr),
            "error": sanitize_sandbox_output(result.error),
            "duration_ms": result.duration_ms,
        }
        if result.stdout_truncated or result.stderr_truncated:
            data["truncated"] = True
        if result.kernel_reset:
            data["notice"] = "内核已重建，之前会话中的变量已丢失"
        if result.workspace_reset:
            data["notice"] = "沙箱已重建，/workspace 中的文件已丢失"
        # 用户代码报错/超时是"执行成功但结果不是 ok"，交给模型自行读 status 修正代码。
        # 但结果本身必须带上失败标记：否则工具节点永远是 completed，
        # 报错的代码在日志/节点状态里和成功无法区分。
        #
        # 为什么用 data["success"] 而不直接返回 error_result：
        # _format_result_for_langchain（langchain_adapter.py）在 ToolResult.success 为 False
        # 时只回传 {success, error, error_code, execution_time}，data 会被整个丢掉，
        # 模型就看不到 status / stderr，无法自行修正代码。
        # 而 agent 的 _tool_failure_reason 读的是序列化后 payload 里的 success，
        # 所以在 data 里给出 success=False，既保住诊断信息，工具节点也能标成 failed。
        #
        # 注意这个折中的代价：ToolResult.success 仍是 True，而
        # ToolService.execute_tool（tool_service.py 的 _record_execution_complete*）
        # 按 ToolResult.success 记 COMPLETED 并且不写 error_message。
        # 目前安全——沙箱工具不在 BUILTIN_TOOLS 里、没有 tool_id，不走 ToolService；
        # 但若日后把沙箱工具登记进工具库或按 ID 挂到工作流工具节点，
        # 必须改成：返回 ToolResult.error_result，并让 _format_result_for_langchain
        # 在失败时把 data 一并回传。
        data["success"] = result.status == "ok"
        if not data["success"] and not data["error"]:
            data["error"] = f"代码未正常结束（status={result.status}）"
        return ToolResult.success_result(data=data, execution_time=_elapsed())

    @staticmethod
    async def _resolve_tenant_id(workspace_id: str) -> Optional[str]:
        from ...db import get_async_db_context
        from ...repositories.tool_repository import ToolRepository

        try:
            async with get_async_db_context() as db:
                tenant_id = await ToolRepository.get_tenant_id_by_workspace_id_async(
                    db, workspace_id
                )
        except Exception as exc:  # noqa: BLE001 - lookup failure must not crash the agent turn
            logger.warning("sandbox_tenant_lookup_failed error=%s", type(exc).__name__)
            return None
        return str(tenant_id) if tenant_id else None
