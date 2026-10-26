"""沙箱文件带出（产物发布）与带入（用户文件推送）

数据流（设计文档 7.5 / 7.6）
----------------------------
- 带出：sandbox-service 的 ``/internal/artifacts/{aid}`` 流式下载 → ``FileStorageService.upload_stream``
  → ``FileMetadata`` 置 completed → 永久下载 URL。沙箱服务本身不碰对象存储。
- 带入：``FileMetadata`` 校验归属 → 存储后端 ``download_stream`` → sandbox-service
  ``PUT /internal/files/upload`` 写入 ``/workspace/sessions/<会话>/in/``。

校验规则放在主应用：扩展名白名单（配置项 ``SANDBOX_FILE_ALLOWED_EXTENSIONS``）、
单文件与单次累计大小、文件头魔数抽查；不信任沙箱给出的 content_type 做放行判断。
"""
from __future__ import annotations

import logging
import os
import posixpath
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ..config import current_settings
from ..integrations.sandbox.client import ArtifactInfo, SandboxPrincipal, SandboxServiceClient
from ..integrations.sandbox.errors import SandboxClientError, SandboxServiceError
from ..storage.exceptions import StorageError

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = "/workspace"

# 带宏的 Office 与可执行/脚本类：给模型一个明确的原因，而不是笼统的"不支持"
_MACRO_EXTENSIONS = frozenset({".pptm", ".xlsm", ".docm", ".potm", ".xltm", ".dotm"})
_EXECUTABLE_EXTENSIONS = frozenset(
    {".exe", ".dll", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1", ".vbs", ".js", ".jar", ".sh", ".so"}
)
# 扩展名 → 文件头魔数（OOXML 与 zip 都以 PK 开头）
_MAGIC: dict[str, bytes] = {
    ".pptx": b"PK",
    ".docx": b"PK",
    ".xlsx": b"PK",
    ".zip": b"PK",
    ".pdf": b"%PDF",
}


class SandboxFileError(Exception):
    """可直接展示给模型的失败原因。"""


@dataclass(frozen=True)
class PublishedFile:
    file_id: str
    name: str
    size: int
    content_type: str
    url: str


@dataclass(frozen=True)
class PublishFailure:
    name: str
    reason: str


@dataclass
class PublishReport:
    published: list[PublishedFile] = field(default_factory=list)
    failed: list[PublishFailure] = field(default_factory=list)


@dataclass(frozen=True)
class ImportedFile:
    file_id: str
    name: str
    sandbox_path: str
    size: int


# ------------------------------------------------------------------ path helpers


def session_dir(conversation_id: str) -> str:
    return f"{WORKSPACE_ROOT}/sessions/{conversation_id}"


def out_dir(conversation_id: str) -> str:
    return f"{session_dir(conversation_id)}/out"


def in_dir(conversation_id: str) -> str:
    return f"{session_dir(conversation_id)}/in"


# --------------------------------------------------------------------- validation


def allowed_extensions() -> frozenset[str]:
    raw = current_settings().sandbox_file_allowed_extensions or ""
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def check_extension(name: str) -> str | None:
    """返回拒绝原因；允许时返回 None。只看最后一个扩展名，不区分大小写。"""

    ext = os.path.splitext(name)[1].lower()
    if ext in _MACRO_EXTENSIONS:
        return "不支持带宏的文件，请输出 .pptx、.xlsx、.docx"
    if ext in _EXECUTABLE_EXTENSIONS:
        return "不支持可执行文件或脚本"
    if ext not in allowed_extensions():
        return "不支持的文件类型"
    return None


def safe_file_name(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")) or "file"
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", base).strip("._") or "file"
    return cleaned[:100]


def describe_error(exc: BaseException) -> str:
    """把各层异常翻成给模型看的一句话（不泄露内部地址或堆栈）。"""

    if isinstance(exc, SandboxFileError):
        return str(exc)
    if isinstance(exc, SandboxServiceError):
        if exc.kind == "artifact_gone":
            return "文件已不在沙箱中或已被修改，需要重新生成"
        if exc.kind == "file_not_found":
            return "沙箱中找不到该文件"
        if exc.kind == "file_too_large":
            return "文件超过大小上限"
        if exc.kind == "path_forbidden":
            return "路径不在 /workspace 之内"
        return f"沙箱服务拒绝了请求（{exc.kind}）"
    if isinstance(exc, SandboxClientError):
        return "沙箱服务不可用"
    if isinstance(exc, StorageError):
        return "文件存储失败"
    if isinstance(exc, FileNotFoundError):
        return "存储中找不到该文件"
    return "文件传输失败"


class _Holder:
    """记录流内部发生的真实异常：存储后端会把它包成 StorageUploadError。"""

    error: BaseException | None = None


async def _guard_stream(
    chunks: AsyncIterator[bytes], ext: str, holder: _Holder
) -> AsyncIterator[bytes]:
    """透传字节，同时抽查文件头魔数。"""

    magic = _MAGIC.get(ext)
    checked = magic is None
    pending = b""
    try:
        async for chunk in chunks:
            if not checked:
                pending += chunk
                if len(pending) < len(magic):  # type: ignore[arg-type]
                    continue
                if not pending.startswith(magic):  # type: ignore[arg-type]
                    raise SandboxFileError("文件内容与扩展名不符，按不支持的文件类型处理")
                checked = True
                chunk, pending = pending, b""
            yield chunk
        if not checked:
            raise SandboxFileError("文件内容与扩展名不符，按不支持的文件类型处理")
    except BaseException as exc:
        holder.error = exc
        raise


# ------------------------------------------------------------------------ publish


async def publish_artifacts(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    artifacts: list[ArtifactInfo],
) -> PublishReport:
    """把一组产物落到主应用存储；单个失败不影响其余文件。"""

    report = PublishReport()
    total = 0
    for art in artifacts:
        if total + art.size > current_settings().sandbox_file_max_total_bytes:
            report.failed.append(PublishFailure(art.name, "超过单次累计大小上限"))
            continue
        outcome = await _publish_one(client, principal, art)
        if isinstance(outcome, PublishedFile):
            total += outcome.size
            report.published.append(outcome)
        else:
            report.failed.append(outcome)
    return report


async def _publish_one(
    client: SandboxServiceClient, principal: SandboxPrincipal, art: ArtifactInfo
) -> PublishedFile | PublishFailure:
    from ..db import get_async_db_context
    from ..models.file_metadata_model import FileMetadata
    from .file_content_service import build_permanent_file_url
    from .file_storage_service import generate_file_key, get_file_storage_service

    reason = check_extension(art.name)
    if reason:
        return PublishFailure(art.name, reason)
    if art.size <= 0:
        return PublishFailure(art.name, "文件为空")
    max_bytes = current_settings().sandbox_file_max_bytes
    if art.size > max_bytes:
        return PublishFailure(
            art.name, f"文件超过 {max_bytes // (1024 * 1024)}MB 上限"
        )

    ext = os.path.splitext(art.name)[1].lower()
    try:
        tenant_uuid = uuid.UUID(principal.tenant_id)
        workspace_uuid = uuid.UUID(principal.workspace_id)
    except ValueError:
        return PublishFailure(art.name, "租户或工作空间标识无效")

    file_id = uuid.uuid4()
    file_key = generate_file_key(tenant_uuid, workspace_uuid, file_id, ext)
    content_type = art.content_type or "application/octet-stream"
    storage_service = get_file_storage_service()
    holder = _Holder()

    async with get_async_db_context() as db:
        meta = FileMetadata(
            id=file_id,
            tenant_id=tenant_uuid,
            workspace_id=workspace_uuid,
            file_key=file_key,
            file_name=art.name[:255],
            file_ext=ext,
            file_size=art.size,
            content_type=content_type,
            status="pending",
        )
        db.add(meta)
        await db.commit()

        try:
            async with client.open_artifact(principal, art.aid) as download:
                written = await storage_service.upload_stream(
                    tenant_uuid,
                    workspace_uuid,
                    file_id,
                    ext,
                    _guard_stream(download.chunks, ext, holder),
                    content_type,
                )
            if written != art.size:
                raise SandboxFileError("传输的字节数与登记大小不符")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a per-file result
            cause = holder.error if holder.error is not None else exc
            logger.warning(
                "sandbox_publish_failed name=%s error=%s", art.name, type(cause).__name__
            )
            meta.status = "failed"
            await db.commit()
            await _discard(storage_service, file_key)
            return PublishFailure(art.name, describe_error(cause))

        meta.status = "completed"
        meta.file_size = written
        await db.commit()

    return PublishedFile(
        file_id=str(file_id),
        name=art.name,
        size=written,
        content_type=content_type,
        url=build_permanent_file_url(file_id),
    )


async def _discard(storage_service, file_key: str) -> None:
    try:
        await storage_service.delete_file(file_key)
    except Exception:  # noqa: BLE001 - cleanup is best effort
        logger.debug("discarding a partial upload failed", exc_info=True)


# ------------------------------------------------------------------------- import


async def import_uploaded_file(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    *,
    conversation_id: str,
    file_id: str,
) -> ImportedFile:
    """把已上传的文件推进沙箱的 ``in/``；失败抛 SandboxFileError / 沙箱客户端异常。"""

    from sqlalchemy import select

    from ..db import get_async_db_context
    from ..models.file_metadata_model import FileMetadata
    from .file_storage_service import get_file_storage_service

    try:
        file_uuid = uuid.UUID(str(file_id))
        tenant_uuid = uuid.UUID(principal.tenant_id)
        workspace_uuid = uuid.UUID(principal.workspace_id)
    except ValueError as exc:
        raise SandboxFileError("file_id 不是有效的文件标识") from exc

    async with get_async_db_context() as db:
        meta = (
            await db.execute(
                select(FileMetadata).where(
                    FileMetadata.id == file_uuid,
                    FileMetadata.tenant_id == tenant_uuid,
                    FileMetadata.workspace_id == workspace_uuid,
                    FileMetadata.status == "completed",
                )
            )
        ).scalar_one_or_none()
        if meta is None:
            raise SandboxFileError("文件不存在，或不属于当前工作空间")
        file_key, name, size = meta.file_key, meta.file_name, int(meta.file_size or 0)

    max_bytes = current_settings().sandbox_file_max_bytes
    if size > max_bytes:
        raise SandboxFileError(
            f"文件超过 {max_bytes // (1024 * 1024)}MB 上限，无法放入沙箱"
        )

    target = f"{in_dir(conversation_id)}/{file_uuid}_{safe_file_name(name)}"
    storage = get_file_storage_service().storage
    try:
        stored = await client.upload_file(
            principal, target, storage.download_stream(file_key), overwrite=True
        )
    except FileNotFoundError as exc:
        raise SandboxFileError("存储中找不到该文件") from exc
    except StorageError as exc:
        raise SandboxFileError("读取存储中的文件失败") from exc
    return ImportedFile(file_id=str(file_uuid), name=name, sandbox_path=target, size=stored)


# ------------------------------------------------------------------------- write

MAX_WRITE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WrittenFile:
    sandbox_path: str
    size: int


def resolve_session_path(conversation_id: str, raw: str) -> str:
    """Resolve a model-supplied path inside the conversation directory.

    A relative path is taken relative to ``/workspace/sessions/<conversation_id>``; an
    absolute path must already lie inside it. ``..`` is normalised first, so it cannot
    climb out. Raises SandboxFileError with a message meant for the model.
    """

    if not isinstance(raw, str) or not raw.strip():
        raise SandboxFileError("path 不能为空")
    text = raw.strip()
    if "\x00" in text:
        raise SandboxFileError("path 含有非法字符")
    base = session_dir(conversation_id)
    path = posixpath.normpath(text if text.startswith("/") else f"{base}/{text}")
    if path == base or not path.startswith(base + "/"):
        raise SandboxFileError(
            f"path 必须是文件路径，且位于当前会话目录内（{base}），可直接写相对路径如 gen_ppt.py 或 out/report.md"
        )
    return path


async def write_text_file(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    *,
    conversation_id: str,
    path: str,
    content: str,
    overwrite: bool = True,
) -> WrittenFile:
    """Write ``content`` (UTF-8) to a file in the conversation directory.

    Used by the model to put scripts and small text files into the sandbox without
    squeezing them through a shell heredoc. Size is capped at ``MAX_WRITE_BYTES``.
    """

    if not isinstance(content, str):
        raise SandboxFileError("content 必须是字符串")
    target = resolve_session_path(conversation_id, path)
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise SandboxFileError(
            f"内容超过 {MAX_WRITE_BYTES // (1024 * 1024)}MB 上限，请拆成多个文件或在沙箱里用代码生成"
        )

    async def chunks() -> AsyncIterator[bytes]:
        yield data

    stored = await client.upload_file(principal, target, chunks(), overwrite=overwrite)
    return WrittenFile(sandbox_path=target, size=stored)


@dataclass(frozen=True)
class EditedFile:
    sandbox_path: str
    size: int
    replaced: int


async def edit_text_file(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    *,
    conversation_id: str,
    path: str,
    old_string: str,
    new_string: str,
) -> EditedFile:
    """Replace one exact occurrence of ``old_string`` in a UTF-8 file of the conversation dir.

    The file is read back, so the model only resends the changed lines. ``old_string``
    must match exactly once; zero or several matches are refused with the count so the
    model can widen the snippet. Read and written sizes are capped at ``MAX_WRITE_BYTES``.
    """

    if not isinstance(old_string, str) or not isinstance(new_string, str):
        raise SandboxFileError("old_string 和 new_string 必须是字符串")
    if old_string == "":
        raise SandboxFileError("old_string 不能为空；要整体覆盖文件请改用 path + content")
    if old_string == new_string:
        raise SandboxFileError("old_string 与 new_string 相同，没有可修改的内容")
    target = resolve_session_path(conversation_id, path)

    raw = await client.read_file(principal, target, max_bytes=MAX_WRITE_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SandboxFileError("该文件不是 UTF-8 文本，无法编辑") from exc

    count = text.count(old_string)
    if count == 0:
        raise SandboxFileError("在文件中没有找到 old_string，请先用终端 cat / grep -n 确认当前内容，注意缩进和换行要完全一致")
    if count > 1:
        raise SandboxFileError(
            f"old_string 在文件中出现了 {count} 次，必须唯一；请带上前后几行上下文让它只匹配一处"
        )

    data = text.replace(old_string, new_string, 1).encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise SandboxFileError(f"修改后的文件超过 {MAX_WRITE_BYTES // (1024 * 1024)}MB 上限")

    async def chunks() -> AsyncIterator[bytes]:
        yield data

    stored = await client.upload_file(principal, target, chunks(), overwrite=True)
    return EditedFile(sandbox_path=target, size=stored, replaced=1)


# -------------------------------------------------------------------------- read

MAX_READ_BYTES = 2 * 1024 * 1024
DEFAULT_READ_LINES = 500
MAX_READ_LINES = 2000
MAX_READ_CHARS = 30000
MAX_LINE_CHARS = 2000


@dataclass(frozen=True)
class ReadFile:
    sandbox_path: str
    content: str  # lines prefixed with their 1-based number
    start_line: int
    end_line: int  # 0 when the slice is empty
    total_lines: int
    truncated: bool  # more lines exist after end_line


async def read_text_file(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    *,
    conversation_id: str,
    path: str,
    start_line: int = 1,
    limit: int = DEFAULT_READ_LINES,
) -> ReadFile:
    """Read a line range of a UTF-8 text file in the conversation directory.

    Read-only. The file is capped at ``MAX_READ_BYTES``; the returned slice is capped by
    ``limit`` lines (at most ``MAX_READ_LINES``) and ``MAX_READ_CHARS`` characters, and
    each line is cut at ``MAX_LINE_CHARS``. Lines come back prefixed with their number so
    the model can pass exact snippets to the replace mode of ``sandbox_write_file``.
    """

    if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
        raise SandboxFileError("start_line 必须是从 1 开始的整数")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise SandboxFileError("limit 必须是正整数")
    limit = min(limit, MAX_READ_LINES)
    target = resolve_session_path(conversation_id, path)

    raw = await client.read_file(principal, target, max_bytes=MAX_READ_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SandboxFileError("该文件不是 UTF-8 文本，无法读取；二进制文件请用终端处理") from exc

    lines = text.splitlines()
    total = len(lines)
    if total and start_line > total:
        raise SandboxFileError(f"start_line 超出范围：文件共 {total} 行")

    out: list[str] = []
    used = 0
    last = 0
    for number in range(start_line, min(start_line + limit, total + 1)):
        line = lines[number - 1]
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "…(行过长已截断)"
        rendered = f"{number}\t{line}"
        if out and used + len(rendered) + 1 > MAX_READ_CHARS:
            break
        out.append(rendered)
        used += len(rendered) + 1
        last = number
    return ReadFile(
        sandbox_path=target,
        content="\n".join(out),
        start_line=start_line,
        end_line=last,
        total_lines=total,
        truncated=last < total,
    )
