"""终端内置工具 - 在独立沙箱服务（sandbox-service）里执行 shell 命令

定位说明
--------
- 命令不在主应用进程里跑，而是通过 ``integrations.sandbox`` 调用 sandbox-service
  的 ``/internal/commands``；沙箱按 (tenant_id, workspace_id, end_users.id) 隔离，
  与代码解释器共用同一台微虚机和 ``/workspace`` 文件系统。
- 每次调用启动一个全新的 ``/bin/bash -lc`` 进程，不保留 shell 状态（cd、export 不会带到下一次），
  没有标准输入和交互式终端；需要跨调用保留的状态请写文件。
- 返回真实的 ``exit_code``，非 0 表示命令失败，交给模型自行读取并修正。
- 运行时上下文由 chat 服务注入：缺少 ``user_id``（end_users.id）、``workspace_id``、
  ``conversation_id`` 任意一项都会拒绝执行，而不是退化成共用一个沙箱。
- 沙箱服务未启用（``ENABLE_SANDBOX_SERVICE=false``）时返回明确的配置错误。
"""
from __future__ import annotations

import logging
import time
from typing import Any, List, Optional

from ...schemas.tool_schema import ParameterType, ToolParameter, ToolResult
from .base import BuiltinTool
from .sandbox_output import clean_terminal_output

logger = logging.getLogger(__name__)

_ANONYMOUS_USER_IDS = {"", "anonymous"}


class TerminalTool(BuiltinTool):
    """终端 - 在隔离沙箱中执行 shell 命令并返回 exit_code / stdout / stderr"""

    @property
    def name(self) -> str:
        return "terminal_tool"

    @property
    def description(self) -> str:
        return (
            "终端：在一台隔离的 Linux 沙箱中执行一条 shell 命令（/bin/bash），返回退出码 exit_code、标准输出和错误输出。"
            "适合运行脚本（如 `python scripts/run.py --arg x`）、查看和操作文件（ls、cat、cp、unzip）、"
            "调用命令行工具（libreoffice、node 等）。常用库已预装，缺库时先用 `pip list` 确认。"
            "每次调用是全新进程：cd、export 状态不会保留，需要时用 cwd 参数或用 && 串联；不支持标准输入和交互式程序。"
            "长时间运行的服务要放到后台并重定向输出，如 `nohup python server.py > /tmp/server.log 2>&1 &`，"
            "否则会一直占用到超时被终止；后台服务只能在沙箱内部访问，沙箱回收后消失。"
            "文件位于 /workspace，同一终端用户的所有会话共享。"
            "exit_code 非 0 表示失败，请阅读 stderr。"
        )

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="command",
                type=ParameterType.STRING,
                description="要执行的一条 shell 命令（最长 64KB），可用 &&、| 串联",
                required=True,
            ),
            ToolParameter(
                name="cwd",
                type=ParameterType.STRING,
                description="工作目录，必须是 /workspace 下的绝对路径；缺省为当前会话目录 /workspace/sessions/<会话ID>",
                required=False,
            ),
            ToolParameter(
                name="timeout",
                type=ParameterType.NUMBER,
                description="执行超时（秒），不能超过沙箱服务的上限；超时会终止整个进程组",
                required=False,
                minimum=1,
                maximum=300,
            ),
        ]

    def resolve_execution_timeout(self, parameters: dict) -> float:
        """命令自身的 timeout 参数 + 30 秒余量；未传时按沙箱服务读超时 330s 再留余量。"""
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

        command = kwargs.get("command")
        cwd = kwargs.get("cwd")
        timeout = kwargs.get("timeout")

        if not isinstance(command, str) or not command.strip():
            return ToolResult.error_result(
                error="command 不能为空", error_code="INVALID_ARGUMENT", execution_time=_elapsed()
            )

        user_id = str(self.get_runtime_context("user_id") or "")
        workspace_id = str(self.get_runtime_context("workspace_id") or "")
        conversation_id = str(self.get_runtime_context("conversation_id") or "")
        if user_id in _ANONYMOUS_USER_IDS or not workspace_id or not conversation_id:
            return ToolResult.error_result(
                error="终端需要在有终端用户、工作空间和会话的对话中使用",
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
            result = await client.run_command(
                principal,
                conversation_id=conversation_id,
                cmd=command,
                cwd=str(cwd) if cwd not in (None, "") else None,
                timeout_s=float(timeout) if timeout not in (None, "") else None,
            )
        except SandboxServiceError as exc:
            logger.warning(
                "sandbox_command_refused kind=%s retryable=%s", exc.kind, exc.retryable
            )
            hint = "，请稍后重试" if exc.retryable else ""
            return ToolResult.error_result(
                error=f"沙箱服务拒绝了请求（{exc.kind}）{hint}",
                error_code=f"SANDBOX_{exc.kind.upper()}",
                execution_time=_elapsed(),
                metadata={"retryable": exc.retryable, "retry_after_s": exc.retry_after_s},
            )
        except SandboxClientError as exc:
            logger.warning("sandbox_command_failed error=%s", type(exc).__name__)
            return ToolResult.error_result(
                error=f"沙箱服务不可用：{exc}",
                error_code="SANDBOX_UNAVAILABLE",
                execution_time=_elapsed(),
            )

        data: dict[str, Any] = {
            "status": result.status,
            "exit_code": result.exit_code,
            "stdout": clean_terminal_output(result.stdout),
            "stderr": clean_terminal_output(result.stderr),
            "error": result.error,
            "duration_ms": result.duration_ms,
        }
        if result.stdout_truncated or result.stderr_truncated:
            data["truncated"] = True
        if result.workspace_reset:
            data["notice"] = "沙箱已重建，/workspace 中的文件已丢失"
        # 命令失败/超时是"执行成功但结果不是 ok"，交给模型自行读 exit_code 与 stderr 修正
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
