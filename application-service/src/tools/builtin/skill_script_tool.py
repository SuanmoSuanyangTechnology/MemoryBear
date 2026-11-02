"""技能脚本执行工具 ``run_skill_script``（§8.6）。

为什么要有这个工具
------------------
技能包要跑脚本时，沙箱里必须先有包。只有技能侧知道"本会话绑定了哪个包"，
所以同步动作必须由一个**知道 call_name 的工具**触发：通用的终端 / 代码解释器
只认会话 ID，无从判断该同步哪个包（要靠解析命令字符串猜路径，很脆）。

注入条件与 ``read_skill`` 一致：应用启用技能 **且** 会话加载集合中存在 ≥1 个
standard 技能。因此普通对话、只读 SKILL.md、元问题都不会分配沙箱实例
—— 首次 ``PUT /internal/files/upload`` 才真正创建实例（懒同步）。

执行面：一律走 ``core/sandbox-service``（Cube 沙箱）
- Python：``/internal/execute``（``code`` 通道，同会话持久内核）
- Node / shell：``/internal/commands``（``shell`` 通道，cwd 指到包目录）
只把 stdout / stderr / exit_code 交回模型（"执行优先"），源码不进上下文。
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from ...schemas.tool_schema import ParameterType, ToolParameter, ToolResult
from .base import BuiltinTool
from .sandbox_output import sanitize_sandbox_output

_ANONYMOUS_USER_IDS = {"", "anonymous"}
# 与沙箱服务 read timeout 对齐：请求超时留余量，避免工具先于沙箱被中止
_DEFAULT_TIMEOUT_S = 360.0
_MAX_TIMEOUT_S = 300.0


class SkillScriptTool(BuiltinTool):
    """run_skill_script - 在隔离沙箱里执行技能包内的一个脚本"""

    @property
    def name(self) -> str:
        return "run_skill_script"

    @property
    def description(self) -> str:
        return (
            "执行技能包内的一个脚本，返回退出码 exit_code、标准输出与错误输出。"
            "技能包会按内容哈希幂等同步到沙箱（同一包只传一次），不用你手动上传或拼路径。"
            "skill 填技能索引里列出的 call_name；script 填包内相对路径（如 scripts/run.py）；"
            "interpreter 支持 python（默认）/ node / shell；args 是传给脚本的命令行参数数组。"
            "exit_code 非 0 表示脚本失败，请阅读 stderr 定位。"
            "脚本在隔离的 Linux 沙箱中运行，出网受白名单限制；文件写入请落在当前会话目录或包目录。"
        )

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="skill",
                type=ParameterType.STRING,
                description="技能标识（技能索引段中列出的 call_name）",
                required=True,
            ),
            ToolParameter(
                name="script",
                type=ParameterType.STRING,
                description="包内脚本的相对路径，如 scripts/run.py、scripts/convert.js、scripts/build.sh",
                required=True,
            ),
            ToolParameter(
                name="interpreter",
                type=ParameterType.STRING,
                description="解释器：python（默认）/ node / shell",
                required=False,
                enum=["python", "node", "shell"],
                default="python",
            ),
            ToolParameter(
                name="args",
                type=ParameterType.ARRAY,
                description="传给脚本的命令行参数数组（如 [\"--input\", \"data.csv\"]）",
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
        """脚本自身 timeout + 30 秒余量；未传时按沙箱服务读超时再留余量。"""
        try:
            requested = float(parameters.get("timeout"))
        except (TypeError, ValueError):
            return _DEFAULT_TIMEOUT_S
        return max(requested, 1.0) + 30.0

    async def execute(self, **kwargs) -> ToolResult:
        start_time = time.time()

        def elapsed() -> float:
            return time.time() - start_time

        from ...integrations.sandbox.client import SandboxPrincipal
        from ...integrations.sandbox.errors import (
            SandboxClientError,
            SandboxConfigurationError,
            SandboxServiceError,
        )
        from ...integrations.sandbox.runtime import get_sandbox_service_client
        from ...services.skill_script_service import SkillScriptError, run_skill_script

        skill = kwargs.get("skill")
        script = kwargs.get("script")
        interpreter = kwargs.get("interpreter") or "python"
        timeout = kwargs.get("timeout")
        raw_args = kwargs.get("args") or []

        if not isinstance(skill, str) or not skill.strip():
            return ToolResult.error_result(
                error="skill 不能为空", error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        if not isinstance(script, str) or not script.strip():
            return ToolResult.error_result(
                error="script 不能为空", error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        if isinstance(raw_args, str):
            raw_args = [raw_args]
        if not isinstance(raw_args, list):
            return ToolResult.error_result(
                error="args 必须是字符串数组", error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )

        user_id = str(self.get_runtime_context("user_id") or "")
        workspace_id = str(self.get_runtime_context("workspace_id") or "")
        conversation_id = str(self.get_runtime_context("conversation_id") or "")
        if user_id in _ANONYMOUS_USER_IDS or not workspace_id or not conversation_id:
            return ToolResult.error_result(
                error="执行技能脚本需要有终端用户、工作空间和会话的对话",
                error_code="SANDBOX_CONTEXT_MISSING",
                execution_time=elapsed(),
            )

        try:
            client = get_sandbox_service_client()
        except SandboxConfigurationError as exc:
            return ToolResult.error_result(
                error=str(exc), error_code="SANDBOX_NOT_ENABLED", execution_time=elapsed()
            )

        tenant_id = await self._resolve_tenant_id(workspace_id)
        if not tenant_id:
            return ToolResult.error_result(
                error="无法确定工作空间所属租户",
                error_code="SANDBOX_TENANT_NOT_FOUND",
                execution_time=elapsed(),
            )

        principal = SandboxPrincipal(
            tenant_id=tenant_id, workspace_id=workspace_id, end_user_id=user_id
        )

        import uuid as _uuid

        from ...db import get_async_db_context

        try:
            timeout_s = float(timeout) if timeout not in (None, "") else None
            async with get_async_db_context() as db:
                outcome = await run_skill_script(
                    client,
                    principal,
                    db,
                    tenant_id=_uuid.UUID(str(tenant_id)),
                    conversation_id=conversation_id,
                    identifier=skill.strip(),
                    script=script.strip(),
                    interpreter=str(interpreter),
                    args=[str(a) for a in raw_args],
                    timeout_s=timeout_s,
                )
        except SkillScriptError as exc:
            return ToolResult.error_result(
                error=str(exc), error_code="SKILL_SCRIPT_INVALID", execution_time=elapsed()
            )
        except SandboxServiceError as exc:
            hint = "，请稍后重试" if exc.retryable else ""
            return ToolResult.error_result(
                error=f"沙箱服务拒绝了请求（{exc.kind}）{hint}",
                error_code=f"SANDBOX_{exc.kind.upper()}",
                execution_time=elapsed(),
                metadata={"retryable": exc.retryable, "retry_after_s": exc.retry_after_s},
            )
        except SandboxClientError as exc:
            return ToolResult.error_result(
                error=f"沙箱服务不可用：{exc}",
                error_code="SANDBOX_UNAVAILABLE",
                execution_time=elapsed(),
            )

        data: dict[str, Any] = {
            "status": outcome.status,
            "exit_code": outcome.exit_code,
            "stdout": sanitize_sandbox_output(outcome.stdout),
            "stderr": sanitize_sandbox_output(outcome.stderr),
            "error": sanitize_sandbox_output(outcome.error),
            "duration_ms": outcome.duration_ms,
            "skill": skill.strip(),
            "script": outcome.script,
            "interpreter": outcome.interpreter,
            "sandbox_dir": outcome.pack_dir,
        }
        if outcome.stdout_truncated or outcome.stderr_truncated:
            data["truncated"] = True
        if outcome.pack_reused:
            data["pack_synced"] = "reused"  # 二次执行不重传
        else:
            data["pack_synced"] = f"{outcome.staged_files} files / {outcome.staged_bytes} bytes"
        if outcome.workspace_reset:
            data["notice"] = "沙箱已重建，/workspace 中的文件已丢失（技能包会在下次执行时自动重新同步）"
        # 脚本失败/超时 = "执行成功但结果不是 ok"，交给模型读 exit_code 与 stderr 自行修正
        return ToolResult.success_result(data=data, execution_time=elapsed())

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
            import logging

            logging.getLogger(__name__).warning(
                "skill_script_tenant_lookup_failed error=%s", type(exc).__name__
            )
            return None
        return str(tenant_id) if tenant_id else None


def create_run_skill_script_tool(
    tenant_id: Any = None,
    *,
    user_id: Any = None,
    workspace_id: Any = None,
    conversation_id: Any = None,
):
    """构造一个 langchain 形态的 run_skill_script 工具（未启用沙箱时返回 None）。"""

    from ...integrations.sandbox.runtime import is_sandbox_integration_enabled

    if not is_sandbox_integration_enabled():
        return None
    instance = SkillScriptTool("skill-runtime:run_skill_script", {})
    instance.operation = instance.name
    instance.set_runtime_context(
        user_id=str(user_id) if user_id is not None else None,
        workspace_id=str(workspace_id) if workspace_id is not None else None,
        conversation_id=str(conversation_id) if conversation_id is not None else None,
    )
    return instance.to_langchain_tool()