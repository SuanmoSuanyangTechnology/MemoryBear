"""MinerU PDF parsing builtin tool (thin wrapper over MinerUV3Client).

Reuses the async /tasks protocol (submit task → poll → fetch result) of the
in-repo ``app.core.rag.chunk.parser.mineru_v3_client.MinerUV3Client``, sharing
the RAG parsing pipeline so agent / workflow node calls yield markdown
semantically consistent with RAG document parsing.

Invocation conventions:
    - Connection info ``api_url`` comes from ``BuiltinToolConfig.parameters``
      (filled in on the tool config page, shared across the tenant; see the
      mineru entry in ``builtin_tools.json``), mapping to
      ``MinerUV3Client.api_server`` / the ``MINERU_V3_APISERVER`` env var.
    - Runtime params ``file_name / file_url / file_content`` come from the caller
      (i.e. the document extractor node); ``start_page_id / end_page_id /
      timeout`` prefer caller values and fall back to the builtin defaults.
    - The return value ``ToolResult.data`` is a dict (JSON serializable):
        {
          "markdown": str,          # parsed markdown text
        }
    With extract_images=True, image URLs are inlined into the markdown image
    references; with extract_images=False image handling is skipped and only
    plain text markdown is returned.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ...rag.chunk.parser.mineru_v3_client import MinerUV3Client, MinerUV3Image
from ..base import ToolParameter, ParameterType, ToolResult
from .base import BuiltinTool


logger = logging.getLogger(__name__)


# Default runtime parameters (used when neither builtin config nor caller specifies)
_DEFAULT_TIMEOUT = 1800
_DEFAULT_START_PAGE_ID = 0
_DEFAULT_END_PAGE_ID = 999_999
_DEFAULT_HEALTH_TIMEOUT = 10
_DEFAULT_EXTRACT_IMAGES = True
# Guard against an LLM passing an arbitrary URL that downloads a multi-GB file into memory
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
# When file_name is one of these placeholder names, infer the real name from the URL
_URL_FALLBACK_FILENAMES = {"file.pdf", "file", "document.pdf", "document", "untitled.pdf"}
# Regex for markdown image references ![alt](src); matches structured_markdown.IMAGE_PATTERN
_IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class MinerUTool(BuiltinTool):
    """MinerU PDF parsing builtin tool (thin wrapper over MinerUV3Client)."""

    @property
    def name(self) -> str:
        return "mineru_tool"

    @property
    def description(self) -> str:
        return (
            "MinerU 文档解析工具:把 PDF、图片、DOCX、PPTX、XLSX 文件解析为 markdown。"
            "内部复用 MinerUV3Client (异步 /tasks 协议),支持起始/结束页范围、"
            "整体超时控制。抽取的图片会上传到存储后端,markdown 中图片引用"
            "会重写为可访问的 URL。"
        )

    # === Builtin config (tenant-level, filled in builtin_tools.json / tool management page) ===

    def get_required_config_parameters(self) -> List[str]:
        return ["api_url"]

    # === Execution-time parameters (runtime overrides fall back to builtin config / defaults) ===

    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="file_name",
                type=ParameterType.STRING,
                description=(
                    "文件名(用于服务端保存与响应定位)。"
                    "若仅传 file_url 且本字段为空,工具会从 URL 推断文件名。"
                ),
                required=False,
            ),
            ToolParameter(
                name="file_url",
                type=ParameterType.FILE,
                description=(
                    "待解析文件。支持:\n"
                    "1. 工作流文件变量如 {{sys.files}} 或 {{start_node.file_var}}\n"
                    "2. http(s) URL 字符串(工具自动下载)\n"
                    "3. 支持 PDF、图片、DOCX、PPTX 或 XLSX 文件\n"
                ),
                required=False,
            ),
            # file_content (bytes)
            ToolParameter(
                name="start_page_id",
                type=ParameterType.INTEGER,
                description="PDF 解析起始页(从 0 开始);默认 0 表示从首页开始。",
                required=False,
                default=_DEFAULT_START_PAGE_ID,
                minimum=0,
            ),
            ToolParameter(
                name="end_page_id",
                type=ParameterType.INTEGER,
                description=(
                    "PDF 解析结束页(从 0 开始,含);默认 999999 表示解析到最后一页。"
                ),
                required=False,
                default=_DEFAULT_END_PAGE_ID,
                minimum=0,
            ),
            ToolParameter(
                name="timeout",
                type=ParameterType.INTEGER,
                description=(
                    "整体解析超时秒数(同时作为 HTTP 请求超时与轮询超时)。"
                    "对大文件 OCR/公式密集场景建议调大;若要分别控制 HTTP 与轮询,"
                    "可通过环境变量 MINERU_V3_REQUEST_TIMEOUT_SECONDS / "
                    "MINERU_V3_POLL_TIMEOUT_SECONDS 覆盖。"
                ),
                required=False,
                default=_DEFAULT_TIMEOUT,
                minimum=1,
                maximum=7200,
            ),
            ToolParameter(
                name="extract_images",
                type=ParameterType.BOOLEAN,
                description=(
                    "是否提取文档中的图片:开启后,图片会上传到存储后端并把可访问的 "
                    "URL 回填到 markdown 图片引用;关闭则只返回文本 "
                    "markdown,跳过图片上传(更快,不占用存储)。"
                ),
                required=False,
                default=_DEFAULT_EXTRACT_IMAGES,
            ),
        ]

    # === Parameter validation ===

    def validate_parameters(self, parameters: Dict[str, Any]) -> Dict[str, str]:
        """Optional numeric parameters are commonly rendered to an empty string
        by workflow templates (a referenced variable resolved to nothing); the
        base validator's ``float('')`` rejects that before ``execute`` /
        ``_resolve_int_runtime_param`` gets a chance to fall back. Here an empty
        string is treated as "not provided" and dropped, letting the runtime
        parameter resolution fall back to config / defaults.
        """
        blankable_optional = {
            p.name for p in self.parameters
            if not p.required and p.type in (ParameterType.INTEGER, ParameterType.NUMBER, ParameterType.BOOLEAN)
        }
        for name in blankable_optional:
            value = parameters.get(name)
            if isinstance(value, str) and value.strip() == "":
                parameters.pop(name)
        return super().validate_parameters(parameters)

    # === Execution entry ===

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Parse a single file via MinerUV3Client and return the markdown string.

        Args:
            **kwargs:
                file_name  (str, recommended — used for server-side storage and response lookup; can be omitted when only file_url is passed, it is then inferred from the URL)
                file_url   (str, optional — http(s) URL, downloaded by the tool before parsing; LLM/Agent entry)
                file_content (bytes, optional — binary passed directly by the document extractor node, highest priority)
                Other override parameters are documented in ``parameters``.

        Raises:
            ValueError: when ``file_name`` / ``file_url`` / ``file_content`` are all empty.

        Returns:
            ToolResult:
                success=True  → data={"markdown": str, "raw": None, "images": list[str]}
                success=False → error=str, error_code="MINERU_ERROR"
        """
        start_time = time.time()
        try:
            api_url = self._resolve_api_url()
            api_key = self.get_config_parameter("api_key")
            if api_key:
                logger.warning(
                    "[MinerUTool] 'api_key' is configured but MinerUV3Client "
                    "does not support authentication; ignoring."
                )

            file_name = kwargs.get("file_name")
            file_content = kwargs.get("file_content")
            file_url = kwargs.get("file_url")

            # ---- Normalize sys.files / FileObject ----
            # The variable pool returns ArrayVariable.value = list[FileVariable],
            # each FileVariable wrapping a FileObject; a single-file variable
            # returns a FileVariable instance. Normalize everything back to a
            # string URL + file_name here.
            from ...workflow.variable.base_variable import FileObject
            from ...workflow.variable.variable_objects import FileVariable

            def _extract_from_file_var(item) -> tuple[str, str, str]:
                """Extract (file_id, url, name) from FileVariable / FileObject / dict."""
                if isinstance(item, FileVariable):
                    fo = item.value  # FileObject
                elif isinstance(item, FileObject):
                    fo = item
                elif isinstance(item, dict) and item.get("is_file"):
                    fo = item
                else:
                    return "", "", ""

                fid = ""
                url = ""
                name = ""
                if isinstance(fo, dict):
                    fid = str(fo.get("file_id") or fo.get("upload_file_id") or "")
                    url = fo.get("url") or ""
                    name = fo.get("name") or ""
                elif hasattr(fo, "file_id"):
                    fid = str(fo.file_id) if fo.file_id else ""
                    url = fo.url or ""
                    name = fo.name or ""

                # When name is empty, derive a default name from file_id
                if not name and fid:
                    name = f"file_{fid}"

                return fid, url, name

            resolved_file_id: str = ""
            if isinstance(file_url, (FileVariable, FileObject)) or (
                isinstance(file_url, dict) and file_url.get("is_file")
            ):
                # Single file variable
                resolved_file_id, file_url_str, name = _extract_from_file_var(file_url)
                if not file_name:
                    file_name = name
                file_url = file_url_str
            elif isinstance(file_url, list) and file_url:
                # Array file variable — take the first file
                first = file_url[0]
                resolved_file_id, file_url_str, name = _extract_from_file_var(first)
                if not file_name:
                    file_name = name
                file_url = file_url_str

            logger.info(
                "[MinerUTool] after normalization: file_name=%r, file_url=%r, "
                "resolved_file_id=%r, file_content_type=%s",
                file_name, file_url, resolved_file_id, type(file_content).__name__
            )

            # With a file_id present, read the bytes straight from the storage
            # backend, bypassing HTTP download (sys.files URLs point at
            # localhost, so a synchronous HTTP request to them would time out)
            if not file_content and resolved_file_id:
                try:
                    file_content = await self._read_file_from_storage(resolved_file_id)
                    logger.info(
                        "[MinerUTool] read %d bytes from storage for file_id=%s",
                        len(file_content), resolved_file_id,
                    )
                except Exception as e:
                    logger.warning(
                        "[MinerUTool] storage read failed for file_id=%s: %s, "
                        "falling back to URL download", resolved_file_id, e,
                    )

            # Require at least one of file_name / file_url / file_content / resolved_file_id
            if not (file_name or file_url or file_content or resolved_file_id):
                raise ValueError(
                    "file_name, file_url, or file_content is required"
                )

            # Fold file_url into file_content, both handled by _resolve_file_url
            if not file_content and file_url:
                file_content = file_url

            # requests is a blocking call (up to 100MB), invoking it directly in
            # the coroutine would stall the whole event loop — run it in a thread
            file_name, file_content = await asyncio.to_thread(
                self._resolve_file_url, file_name, file_content
            )

            if not isinstance(file_content, bytes) or not file_content:
                raise RuntimeError(f"[MinerUTool] empty file binary (type={type(file_content).__name__})")

            # File type check: MinerU only supports PDF, images, DOCX, PPTX, XLSX
            supported_extensions = {
                ".pdf",
                ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp",
                ".docx",
                ".pptx",
                ".xlsx",
            }
            ext = Path(file_name).suffix.lower() if file_name else ""

            # Without an extension, sniff the real type from magic bytes
            if not ext and isinstance(file_content, bytes) and len(file_content) >= 8:
                # PDF: %PDF-
                if file_content.startswith(b"%PDF-"):
                    ext = ".pdf"
                    file_name = f"{file_name}.pdf"
                # DOCX/PPTX/XLSX: ZIP container, PK header
                elif file_content.startswith(b"PK\x03\x04"):
                    # A precise check would inspect content_types inside the ZIP;
                    # simplified here to accept all Office Open XML formats
                    ext = ".docx"  # assume docx first, the server decides precisely
                # PNG: 89 50 4E 47
                elif file_content.startswith(b"\x89PNG"):
                    ext = ".png"
                    file_name = f"{file_name}.png"
                # JPEG: FF D8 FF
                elif file_content.startswith(b"\xFF\xD8\xFF"):
                    ext = ".jpg"
                    file_name = f"{file_name}.jpg"
                # GIF: 47 49 46 38
                elif file_content.startswith(b"GIF8"):
                    ext = ".gif"
                    file_name = f"{file_name}.gif"
                # BMP: 42 4D
                elif file_content.startswith(b"BM"):
                    ext = ".bmp"
                    file_name = f"{file_name}.bmp"

            # Final check: extension must exist and be supported
            if not ext:
                raise ValueError(
                    f"无法识别文件类型: {file_name}。"
                    f"MinerU 支持的格式: {', '.join(sorted(supported_extensions))}"
                )
            if ext not in supported_extensions:
                supported_list = ", ".join(sorted(supported_extensions))
                raise ValueError(
                    f"MinerU 不支持的文件类型: {ext} (文件: {file_name})。"
                    f"支持的格式: {supported_list}"
                )

            # Runtime parameters: execute kwargs > builtin configuration > builtin defaults
            start_page_id = self._resolve_int_runtime_param(
                "start_page_id", kwargs, _DEFAULT_START_PAGE_ID
            )
            end_page_id = self._resolve_int_runtime_param(
                "end_page_id", kwargs, _DEFAULT_END_PAGE_ID
            )
            timeout = self._resolve_int_runtime_param(
                "timeout", kwargs, _DEFAULT_TIMEOUT
            )
            extract_images = self._resolve_bool_runtime_param(
                "extract_images", kwargs, _DEFAULT_EXTRACT_IMAGES
            )

            client = MinerUV3Client(
                api_server=api_url,
                request_timeout_seconds=float(timeout),
                poll_timeout_seconds=float(timeout),
            )

            # MinerUV3Client.parse uses the async /tasks protocol internally with
            # a blocking HTTP / polling loop; run it in the default thread pool
            # to avoid stalling the event loop. Unlike parse_to_markdown, parse
            # also returns decoded images (MinerUV3Result.images) for upload below.
            mineru_result = await asyncio.to_thread(
                client.parse,
                file_name,
                file_content,
                int(start_page_id),
                int(end_page_id),
                None,  # callback: no LLM progress callback needed in tool context
            )
            markdown = mineru_result.markdown

            execution_time = time.time() - start_time
            logger.info(
                "[MinerUTool] parsed: file=%s markdown_chars=%d images=%d",
                file_name, len(markdown), len(mineru_result.images),
            )

            if extract_images and mineru_result.images:
                markdown, _ = await self._store_images_and_rewrite_markdown(
                    markdown, mineru_result.images
                )

            # Only markdown is returned; image URLs are inlined in its image references
            return ToolResult.success_result(
                data={"markdown": markdown},
                execution_time=execution_time,
            )

        except Exception as e:
            logger.error(f"[MinerUTool] failed: {e}", exc_info=True)
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="MINERU_ERROR",
                execution_time=execution_time,
            )

    # === Client instantiation helpers ===

    def _resolve_api_url(self) -> str:
        """Read and validate the ``api_url`` config; raises ValueError on failure."""
        api_url = self.get_config_parameter("api_url")
        if not api_url:
            raise ValueError(
                "MinerU builtin tool is not configured: missing required "
                "configuration parameter 'api_url'"
            )
        api_url = str(api_url).strip().rstrip("/")
        if not (api_url.startswith("http://") or api_url.startswith("https://")):
            raise ValueError("api_url must start with http:// or https://")
        return api_url

    # === Image upload helpers ===

    async def _read_file_from_storage(self, file_id: str) -> bytes:
        """Read file bytes directly by file_id from the storage backend,
        bypassing HTTP download.

        sys.files URLs point at localhost:8000/storage/permanent/{file_id}, so a
        synchronous HTTP request to them would time out. Look up FileMetadata
        for the file_key instead, then read the storage backend through
        FileStorageService.download_file.
        """
        from ...db import get_db_read
        from ...models.file_metadata_model import FileMetadata
        from ...services.file_storage_service import FileStorageService

        file_uuid = uuid.UUID(file_id)
        with get_db_read() as db:
            meta = db.query(FileMetadata).filter(FileMetadata.id == file_uuid).first()
            if not meta:
                raise FileNotFoundError(f"file_id={file_id} not found in file_metadata")
            file_key = meta.file_key

        storage = FileStorageService()
        return await storage.download_file(file_key)

    def _resolve_tenant_id(self) -> Optional[uuid.UUID]:
        """Resolve tenant_id from the runtime context's workspace_id (the tool
        itself carries no tenant information)."""
        workspace_id = self.get_runtime_context("workspace_id")
        if not workspace_id:
            return None
        try:
            workspace_uuid = workspace_id if isinstance(workspace_id, uuid.UUID) else uuid.UUID(str(workspace_id))
        except (TypeError, ValueError):
            return None

        from ...db import get_db_read
        from ...repositories.tool_repository import ToolRepository

        with get_db_read() as db:
            return ToolRepository.get_tenant_id_by_workspace_id(db, str(workspace_uuid))

    async def _store_images_and_rewrite_markdown(
        self, markdown: str, images: Dict[str, MinerUV3Image]
    ) -> tuple[str, List[str]]:
        """Upload the images extracted by MinerU to the existing storage backend,
        rewrite the image references in the markdown to the uploaded URLs, and
        return the deduplicated URL list.

        Uploading needs tenant_id/workspace_id; when they cannot be resolved the
        upload is skipped and the markdown is returned unchanged (image
        references then still point at temporary MinerU server file names, which
        are unreachable, but the text content is unaffected).
        """
        tenant_id = self._resolve_tenant_id()
        workspace_id = self.get_runtime_context("workspace_id")
        if tenant_id is None:
            logger.warning(
                "[MinerUTool] skip image upload: cannot resolve tenant_id from "
                "workspace_id=%r", workspace_id,
            )
            return markdown, []

        from ...services.file_storage_service import FileStorageService

        storage_service = FileStorageService()
        url_by_src: Dict[str, str] = {}
        for src_name, mineru_image in images.items():
            try:
                url = await self._upload_single_image(
                    storage_service, tenant_id, workspace_id, mineru_image
                )
                url_by_src[src_name] = url
            except Exception as e:
                logger.error(
                    "[MinerUTool] failed to upload image %s: %s", src_name, e, exc_info=True
                )

        if not url_by_src:
            return markdown, []

        def _replace(match: "re.Match[str]") -> str:
            alt, src = match.group(1), match.group(2)
            url = url_by_src.get(Path(src).name)
            if url is None:
                return match.group(0)
            return f"![{alt}]({url})"

        rewritten_markdown = _IMAGE_MARKDOWN_PATTERN.sub(_replace, markdown)
        # Deduplicate while preserving order
        image_urls = list(dict.fromkeys(url_by_src.values()))
        return rewritten_markdown, image_urls

    @staticmethod
    async def _upload_single_image(
        storage_service: Any,
        tenant_id: uuid.UUID,
        workspace_id: Any,
        mineru_image: MinerUV3Image,
    ) -> str:
        from ...config import current_settings
        from ...services.file_storage_service import generate_file_key

        workspace_uuid: Optional[uuid.UUID] = None
        if workspace_id:
            try:
                workspace_uuid = workspace_id if isinstance(workspace_id, uuid.UUID) else uuid.UUID(str(workspace_id))
            except (TypeError, ValueError):
                workspace_uuid = None

        file_id = uuid.uuid4()
        file_ext = mineru_image.file_ext or ".png"
        await storage_service.upload_file(
            tenant_id=tenant_id,
            workspace_id=workspace_uuid,
            file_id=file_id,
            file_ext=file_ext,
            content=mineru_image.binary,
            content_type=mineru_image.content_type,
        )

        from ...db import get_db_context
        from ...models.file_metadata_model import FileMetadata

        file_key = generate_file_key(tenant_id, workspace_uuid, file_id, file_ext)
        with get_db_context() as db:
            db.add(FileMetadata(
                id=file_id,
                tenant_id=tenant_id,
                workspace_id=workspace_uuid,
                file_key=file_key,
                file_name=mineru_image.name or f"{file_id}{file_ext}",
                file_ext=file_ext,
                file_size=len(mineru_image.binary),
                content_type=mineru_image.content_type,
                status="completed",
            ))
            db.commit()

        server_url = (current_settings().file_local_server_url or "").rstrip("/")
        return f"{server_url}/storage/permanent/{file_id}"

    # === File download helper (turn a file_url into bytes in agent scenarios) ===

    @staticmethod
    def _resolve_file_url(
        file_name: Any, file_content: Any
    ) -> tuple[Any, Any]:
        """When ``file_content`` looks like an http(s) URL, download it to real bytes first.

        This is a convenience layer for agent/LLM scenarios — the model only has
        a URL, not the real file bytes, so it can pass the URL through as
        ``file_content`` and the tool downloads it internally.

        Nodes passing ``bytes`` directly are fully unaffected.
        """
        if not isinstance(file_content, str):
            return file_name, file_content
        url = file_content.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            # Not a URL — keep the original behavior (MinerUV3Client raises for non-bytes downstream)
            return file_name, file_content

        logger.info(f"[MinerUTool] downloading file from URL: {url}")
        try:
            with requests.get(
                url,
                stream=True,
                timeout=_DEFAULT_HEALTH_TIMEOUT,
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                # Pre-check Content-Length to avoid blowing up memory in LLM scenarios
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) > _MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"remote file too large: Content-Length={cl} "
                        f"> max={_MAX_DOWNLOAD_BYTES}"
                    )
                chunks: List[bytes] = []
                total = 0
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"remote file exceeds max size {_MAX_DOWNLOAD_BYTES} bytes"
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)
        except requests.RequestException as e:
            raise ValueError(f"failed to download file from URL {url}: {e}") from e

        # If the caller omitted file_name or passed a placeholder, infer the
        # real name from the URL
        if (
            not file_name
            or str(file_name).strip().lower() in _URL_FALLBACK_FILENAMES
        ):
            guessed = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
            if not guessed:
                guessed = "downloaded_file"
            file_name = guessed

        logger.info(
            f"[MinerUTool] downloaded {len(data)} bytes from URL, "
            f"resolved file_name={file_name}"
        )
        return file_name, data

    # === Runtime parameter resolution ===

    def _resolve_int_runtime_param(
        self, name: str, kwargs: Dict[str, Any], default: int
    ) -> int:
        """Read an int runtime parameter, falling back to default on failure.

        Compatibility note: workflow template rendering / form input frequently
        pass numbers as strings (e.g. "0"); the tool converts with ``int()``
        itself, decoupled from ``tools/base.py._validate_parameter_type`` —
        even if upper-layer validation strictly rejects string numbers, the
        mineru tool still parses them fine. Truly unparseable input (non-numeric
        strings, None, ...) raises ``ValueError`` explicitly rather than
        silently falling back to default and surprising the user.
        """
        raw: Any = None
        if name in kwargs and kwargs[name] is not None and kwargs[name] != "":
            raw = kwargs[name]
        else:
            cfg_value = self.get_config_parameter(name)
            if cfg_value is not None and cfg_value != "":
                raw = cfg_value
        if raw is None:
            return default
        if isinstance(raw, bool):
            # bool is an int subclass (True=1/False=0); passing one as a number
            # is almost certainly a caller mistake — reject explicitly instead
            # of silently turning it into 0/1
            raise ValueError(
                f"[MinerUTool] {name!r} must be an integer, "
                f"got bool: {raw!r}"
            )
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError as e:
                raise ValueError(
                    f"[MinerUTool] {name!r} must be an integer, "
                    f"got unparseable string: {raw!r}"
                ) from e
        raise ValueError(
            f"[MinerUTool] {name!r} must be an integer, "
            f"got {type(raw).__name__}: {raw!r}"
        )

    def _resolve_bool_runtime_param(
        self, name: str, kwargs: Dict[str, Any], default: bool
    ) -> bool:
        """Read a bool runtime parameter, falling back to default on failure.

        Compatibility note same as ``_resolve_int_runtime_param``: workflow
        template rendering commonly passes booleans as strings
        ("true"/"false"/"1"/"0"), recognized explicitly here.
        """
        raw: Any = None
        if name in kwargs and kwargs[name] is not None and kwargs[name] != "":
            raw = kwargs[name]
        else:
            cfg_value = self.get_config_parameter(name)
            if cfg_value is not None and cfg_value != "":
                raw = cfg_value
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in ("true", "1", "yes"):
                return True
            if normalized in ("false", "0", "no"):
                return False
            raise ValueError(
                f"[MinerUTool] {name!r} must be a boolean, "
                f"got unparseable string: {raw!r}"
            )
        raise ValueError(
            f"[MinerUTool] {name!r} must be a boolean, "
            f"got {type(raw).__name__}: {raw!r}"
        )

    # === Connection test ===

    async def test_connection(self) -> Dict[str, Any]:
        """Probe the MinerUV3 service's root /health endpoint to verify connectivity."""
        try:
            api_url = self._resolve_api_url()
        except Exception as e:
            return {"success": False, "message": f"配置无效: {e}"}

        health_url = f"{api_url}/health"
        try:
            resp = await asyncio.to_thread(
                requests.get,
                health_url,
                headers={"Accept": "application/json"},
                timeout=float(_DEFAULT_HEALTH_TIMEOUT),
            )
        except requests.RequestException as e:
            return {
                "success": False,
                "message": f"无法访问 {health_url}: {e}",
                "api_url": api_url,
            }

        if resp.status_code != 200:
            return {
                "success": False,
                "message": f"{health_url} 返回 HTTP {resp.status_code}: {resp.text[:200]}",
                "api_url": api_url,
            }

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        return {
            "success": True,
            "message": "MinerU 服务连接正常",
            "api_url": api_url,
            "service_status": (payload or {}).get("status"),
            "service_version": (payload or {}).get("version"),
        }
