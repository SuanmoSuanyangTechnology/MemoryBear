"""沙箱文件内置工具：读写会话文件，并将沙箱产物交付给用户。

- ``sandbox_publish_file``：沙箱 ``/workspace`` 下的文件 → 主应用文件存储 → 永久下载链接。
- ``sandbox_write_file``：把模型给出的文本写成会话目录内的文件（path + content），或精确替换其中一段（path + old_string + new_string）。
- ``sandbox_read_file``：只读，按行范围读取会话目录内的文本文件。

按关键字查找、列目录不另做工具：直接用终端（grep -n / ls）或代码解释器即可。
校验与传输逻辑在 ``services.sandbox_file_service``；运行时上下文与代码解释器一致，
缺少 user_id / workspace_id / conversation_id 任意一项都拒绝执行。
"""
from __future__ import annotations

import logging
import posixpath
import shlex
import time
from typing import Any, List, Optional

from ...schemas.tool_schema import ParameterType, ToolParameter, ToolResult
from .base import BuiltinTool

logger = logging.getLogger(__name__)

_ANONYMOUS_USER_IDS = {"", "anonymous"}
_WORKSPACE = "/workspace"


class _SandboxFileToolBase(BuiltinTool):
    """公共部分：身份解析、沙箱客户端获取与错误翻译。"""

    # 文件读写是一次 HTTP 往返加租户查询；整体兜底，避免上传/查询挂住时前端一直转
    execution_timeout_s: float = 90.0

    def resolve_execution_timeout(self, parameters: dict) -> float:
        return self.execution_timeout_s

    async def _prepare(self, started: float):
        """返回 (client, principal, conversation_id, None) 或 (None, None, None, 错误结果)。"""
        from ...integrations.sandbox.client import SandboxPrincipal
        from ...integrations.sandbox.errors import SandboxConfigurationError
        from ...integrations.sandbox.runtime import get_sandbox_service_client

        def fail(error: str, code: str):
            return None, None, None, ToolResult.error_result(
                error=error, error_code=code, execution_time=time.time() - started
            )

        user_id = str(self.get_runtime_context("user_id") or "")
        workspace_id = str(self.get_runtime_context("workspace_id") or "")
        conversation_id = str(self.get_runtime_context("conversation_id") or "")
        if user_id in _ANONYMOUS_USER_IDS or not workspace_id or not conversation_id:
            return fail("沙箱文件工具需要在有终端用户、工作空间和会话的对话中使用", "SANDBOX_CONTEXT_MISSING")
        try:
            client = get_sandbox_service_client()
        except SandboxConfigurationError as exc:
            return fail(str(exc), "SANDBOX_NOT_ENABLED")
        tenant_id = await self._resolve_tenant_id(workspace_id)
        if not tenant_id:
            return fail("无法确定工作空间所属租户", "SANDBOX_TENANT_NOT_FOUND")
        principal = SandboxPrincipal(
            tenant_id=tenant_id, workspace_id=workspace_id, end_user_id=user_id
        )
        return client, principal, conversation_id, None

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


class SandboxPublishFileTool(_SandboxFileToolBase):
    """把沙箱里的文件交付给用户，返回下载链接"""

    # 复制(60s) + 最多 100MB 的流式转存
    execution_timeout_s: float = 300.0

    @property
    def name(self) -> str:
        return "sandbox_publish_file"

    @property
    def description(self) -> str:
        return (
            "把沙箱 /workspace 下的一个文件交付给用户：文件会存入平台文件存储，返回 file_id 和下载链接，"
            "你需要把链接写进回复里给用户。生成了 PPT、Word、Excel、PDF、图片、CSV 等成品后调用它；"
            "只在文件已写完整后调用。放行类型：pptx/docx/xlsx/pdf/csv/txt/md/json/png/jpg/gif/webp/svg/zip/ppt/doc/xls，"
            "带宏的 Office 文件和可执行文件会被拒绝。单个文件不超过 100MB。"
            "文件不在会话的 out/ 目录时会先被复制过去。"
        )

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="要交付的文件路径：相对当前会话目录（如 out/report.xlsx、report.pptx），或 /workspace 之下的绝对路径",
                required=True,
            )
        ]

    async def execute(self, **kwargs) -> ToolResult:
        started = time.time()

        def elapsed() -> float:
            return time.time() - started

        from ...integrations.sandbox.errors import SandboxClientError
        from ...services import sandbox_file_service as svc

        raw = kwargs.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return ToolResult.error_result(
                error="path 不能为空", error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        client, principal, conversation_id, failure = await self._prepare(started)
        if failure is not None:
            return failure

        text = raw.strip()
        # 相对路径按当前会话目录解析（与终端、代码解释器的默认目录一致）
        path = posixpath.normpath(
            text if text.startswith("/") else f"{svc.session_dir(conversation_id)}/{text}"
        )
        if path != _WORKSPACE and not path.startswith(_WORKSPACE + "/"):
            return ToolResult.error_result(
                error="path 必须是当前会话目录内的相对路径，或 /workspace 之下的绝对路径",
                error_code="INVALID_ARGUMENT",
                execution_time=elapsed(),
            )

        out = svc.out_dir(conversation_id)
        try:
            if posixpath.dirname(path) != out:
                target = f"{out}/{posixpath.basename(path)}"
                copy = await client.run_command(
                    principal,
                    conversation_id=conversation_id,
                    cmd=f"mkdir -p {shlex.quote(out)} && cp -f -- {shlex.quote(path)} {shlex.quote(target)}",
                    timeout_s=60,
                )
                if copy.exit_code != 0:
                    return ToolResult.error_result(
                        error=f"无法读取该文件：{(copy.stderr or copy.error or '复制失败').strip()[:200]}",
                        error_code="SANDBOX_FILE_NOT_FOUND",
                        execution_time=elapsed(),
                    )
                path = target
            artifacts, _ = await client.list_artifacts(principal, conversation_id)
            match = [a for a in artifacts if a.path == path]
            if not match:
                return ToolResult.error_result(
                    error="沙箱中找不到该文件，或文件已被移走",
                    error_code="SANDBOX_FILE_NOT_FOUND",
                    execution_time=elapsed(),
                )
            report = await svc.publish_artifacts(client, principal, match)
        except SandboxClientError as exc:
            logger.warning("sandbox_publish_failed error=%s", type(exc).__name__)
            return ToolResult.error_result(
                error=svc.describe_error(exc),
                error_code="SANDBOX_UNAVAILABLE",
                execution_time=elapsed(),
            )

        if not report.published:
            reason = report.failed[0].reason if report.failed else "文件传输失败"
            return ToolResult.error_result(
                error=f"文件未能交付：{reason}",
                error_code="SANDBOX_PUBLISH_FAILED",
                execution_time=elapsed(),
            )
        item = report.published[0]
        data: dict[str, Any] = {
            "file_id": item.file_id,
            "name": item.name,
            "size": item.size,
            "content_type": item.content_type,
            "download_url": item.url,
        }
        return ToolResult.success_result(data=data, execution_time=elapsed())


def _as_int(value: Any, default: int) -> Any:
    """Tool arguments may arrive as strings; empty means 'use the default'."""

    if value is None or value == "":
        return default
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return value


class SandboxReadFileTool(_SandboxFileToolBase):
    """只读：按行范围读取沙箱会话目录内的文本文件"""

    @property
    def name(self) -> str:
        return "sandbox_read_file"

    @property
    def description(self) -> str:
        return (
            "读取沙箱当前会话目录内的一个 UTF-8 文本文件（脚本、日志、配置、CSV 等），只读。"
            "path 是相对会话目录的路径（如 gen_ppt.py、out/report.md），不能越出会话目录。"
            "返回的每一行前带有行号（行号和正文之间是制表符，行号不属于文件内容）；"
            "默认从第 1 行读 500 行，用 start_line 和 limit 翻页，单次最多 2000 行、约 3 万字符，过长的行会被截断。"
            "文件不超过 2MB，二进制文件（pptx、xlsx、图片等）不能读，请用终端或代码解释器处理。"
            "按关键字查找请用终端的 grep -n，列目录请用终端的 ls。"
            "修改文件内容用 sandbox_write_file。"
        )

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="要读取的文件路径，相对当前会话目录（如 gen_ppt.py）",
                required=True,
            ),
            ToolParameter(
                name="start_line",
                type=ParameterType.INTEGER,
                description="起始行号，从 1 开始，默认 1",
                required=False,
                default=1,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="最多读取的行数，默认 500，上限 2000",
                required=False,
                default=500,
            ),
        ]

    async def execute(self, **kwargs) -> ToolResult:
        started = time.time()

        def elapsed() -> float:
            return time.time() - started

        from ...integrations.sandbox.errors import SandboxClientError
        from ...services import sandbox_file_service as svc

        path = kwargs.get("path")
        if not isinstance(path, str) or not path.strip():
            return ToolResult.error_result(
                error="path 不能为空", error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        start_line = _as_int(kwargs.get("start_line"), 1)
        limit = _as_int(kwargs.get("limit"), svc.DEFAULT_READ_LINES)

        client, principal, conversation_id, failure = await self._prepare(started)
        if failure is not None:
            return failure
        try:
            result = await svc.read_text_file(
                client,
                principal,
                conversation_id=conversation_id,
                path=path,
                start_line=start_line,
                limit=limit,
            )
        except svc.SandboxFileError as exc:
            return ToolResult.error_result(
                error=str(exc), error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        except SandboxClientError as exc:
            logger.warning("sandbox_read_failed error=%s", type(exc).__name__)
            missing = getattr(exc, "kind", None) == "file_not_found"
            return ToolResult.error_result(
                error=svc.describe_error(exc),
                error_code="SANDBOX_FILE_NOT_FOUND" if missing else "SANDBOX_UNAVAILABLE",
                execution_time=elapsed(),
            )
        data: dict[str, Any] = {
            "sandbox_path": result.sandbox_path,
            "content": result.content,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "total_lines": result.total_lines,
            "truncated": result.truncated,
        }
        if result.truncated:
            data["next_start_line"] = result.end_line + 1
        return ToolResult.success_result(data=data, execution_time=elapsed())


class SandboxWriteFileTool(_SandboxFileToolBase):
    """写文本文件到沙箱会话目录：整文件写入（path + content），或精确替换（path + old_string + new_string）"""

    @property
    def name(self) -> str:
        return "sandbox_write_file"

    @property
    def description(self) -> str:
        return (
            "在沙箱当前会话目录内写入或修改一个 UTF-8 文本文件，两种用法互斥，每次只用一种："
            "（1）传 path + content：把一段文本（脚本、配置、数据）整份写成文件，已存在会被覆盖，content 不超过 2MB。"
            "需要运行较长的程序（生成 PPT/Word/Excel 等）时，先用它把脚本写成文件，再用终端 `python 文件名` 执行；"
            "终端和代码解释器的默认目录就是会话目录，所以相对路径能直接对上。"
            "（2）传 path + old_string + new_string：精确修改已有文本文件，把 old_string 替换成 new_string，"
            "脚本报错后只改出错的几行，不用整份重发。old_string 必须在文件中恰好出现一次（带上前后几行上下文以保证唯一），"
            "缩进和换行要完全一致；new_string 为空串表示删除。文件需是 UTF-8 文本且不超过 2MB。"
            "path 是相对会话目录的路径（如 gen_ppt.py、data/input.json），不能越出会话目录。"
            "本工具不读取、不搜索：查看内容用 sandbox_read_file，按关键字查找（grep -n）用终端。"
        )

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type=ParameterType.STRING,
                description="目标文件路径，相对当前会话目录（如 gen_ppt.py）",
                required=True,
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="整文件写入：文件的全部文本内容（UTF-8，不超过 2MB）。与 old_string/new_string 二选一",
                required=False,
            ),
            ToolParameter(
                name="old_string",
                type=ParameterType.STRING,
                description="局部替换：要被替换的原文，必须在文件中恰好出现一次，需与 new_string 一起传",
                required=False,
            ),
            ToolParameter(
                name="new_string",
                type=ParameterType.STRING,
                description="局部替换：替换后的新文本，空串表示删除 old_string",
                required=False,
            ),
        ]

    async def execute(self, **kwargs) -> ToolResult:
        started = time.time()

        def elapsed() -> float:
            return time.time() - started

        path = kwargs.get("path")
        content = kwargs.get("content")
        old_string = kwargs.get("old_string")
        new_string = kwargs.get("new_string")

        if old_string not in (None, "") or new_string not in (None, ""):
            if content not in (None, ""):
                return ToolResult.error_result(
                    error="局部替换（old_string/new_string）不能与 content 同时使用",
                    error_code="INVALID_ARGUMENT",
                    execution_time=elapsed(),
                )
            return await self._edit_text(path, old_string, new_string if new_string is not None else "", started)
        return await self._write_text(path, content, started)

    async def _write_text(self, path: Any, content: Any, started: float) -> ToolResult:
        from ...integrations.sandbox.errors import SandboxClientError
        from ...services import sandbox_file_service as svc

        def elapsed() -> float:
            return time.time() - started

        if not isinstance(path, str) or not path.strip() or not isinstance(content, str):
            return ToolResult.error_result(
                error="写文件需要同时提供 path 和 content（字符串）",
                error_code="INVALID_ARGUMENT",
                execution_time=elapsed(),
            )
        client, principal, conversation_id, failure = await self._prepare(started)
        if failure is not None:
            return failure
        try:
            written = await svc.write_text_file(
                client, principal, conversation_id=conversation_id, path=path, content=content
            )
        except svc.SandboxFileError as exc:
            return ToolResult.error_result(
                error=str(exc), error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        except SandboxClientError as exc:
            logger.warning("sandbox_write_failed error=%s", type(exc).__name__)
            return ToolResult.error_result(
                error=svc.describe_error(exc),
                error_code="SANDBOX_UNAVAILABLE",
                execution_time=elapsed(),
            )
        return ToolResult.success_result(
            data={"sandbox_path": written.sandbox_path, "size": written.size},
            execution_time=elapsed(),
        )

    async def _edit_text(self, path: Any, old_string: Any, new_string: Any, started: float) -> ToolResult:
        from ...integrations.sandbox.errors import SandboxClientError
        from ...services import sandbox_file_service as svc

        def elapsed() -> float:
            return time.time() - started

        if (
            not isinstance(path, str)
            or not path.strip()
            or not isinstance(old_string, str)
            or not isinstance(new_string, str)
        ):
            return ToolResult.error_result(
                error="局部替换需要同时提供 path、old_string 和 new_string（字符串，new_string 可为空串表示删除）",
                error_code="INVALID_ARGUMENT",
                execution_time=elapsed(),
            )
        client, principal, conversation_id, failure = await self._prepare(started)
        if failure is not None:
            return failure
        try:
            edited = await svc.edit_text_file(
                client,
                principal,
                conversation_id=conversation_id,
                path=path,
                old_string=old_string,
                new_string=new_string,
            )
        except svc.SandboxFileError as exc:
            return ToolResult.error_result(
                error=str(exc), error_code="INVALID_ARGUMENT", execution_time=elapsed()
            )
        except SandboxClientError as exc:
            logger.warning("sandbox_edit_failed error=%s", type(exc).__name__)
            missing = getattr(exc, "kind", None) == "file_not_found"
            return ToolResult.error_result(
                error=svc.describe_error(exc),
                error_code="SANDBOX_FILE_NOT_FOUND" if missing else "SANDBOX_UNAVAILABLE",
                execution_time=elapsed(),
            )
        return ToolResult.success_result(
            data={"sandbox_path": edited.sandbox_path, "size": edited.size, "replaced": edited.replaced},
            execution_time=elapsed(),
        )
