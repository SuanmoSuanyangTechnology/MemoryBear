"""MinerU PDF 解析内置工具（MinerUV3Client 薄包装）。

走仓内既有的 ``app.core.rag.chunk.parser.mineru_v3_client.MinerUV3Client``
的异步 /tasks 协议（任务提交 → 轮询 → 拉取结果），与 RAG 解析管道同源,
保证 agent / 工作流节点调用与 RAG 文档解析得到的 markdown 语义一致。

调用约定:
    - 连接信息 ``api_url`` 来自 ``BuiltinToolConfig.parameters``
      （在工具配置页面里填写，全租户共享；详见 ``builtin_tools.json`` 的
      mineru 条目），对应 ``MinerUV3Client.api_server`` / 环境变量
      ``MINERU_V3_APISERVER``。
    - 运行时参数 ``file_name / file_url / file_content`` 来自调用方（即文档
      提取器节点），``start_page_id / end_page_id / timeout`` 优先取调用方
      传入，缺省回落到内置默认值。
    - 返回值 ``ToolResult.data`` 为 dict，结构(JSON 可序列化):
        {
          "markdown": str,          # 解析后的 markdown 文本
        }
    当 extract_images=True 时,图片 URL 会内嵌到 markdown 的图片引用中;
    extract_images=False 时跳过图片处理,只返回纯文本 markdown。
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

from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Client, MinerUV3Image
from app.core.tools.base import ToolParameter, ToolResult, ParameterType
from app.core.tools.builtin.base import BuiltinTool


logger = logging.getLogger(__name__)


# 默认运行时参数（builtin 配置 / 调用方均未指定时使用）
_DEFAULT_TIMEOUT = 1800
_DEFAULT_START_PAGE_ID = 0
_DEFAULT_END_PAGE_ID = 999_999
_DEFAULT_HEALTH_TIMEOUT = 10
_DEFAULT_EXTRACT_IMAGES = True
# 防止 LLM 在 agent 场景下随便给个 URL 把几 GB 的文件下载到内存里
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
# 当 file_name 是这种占位名时,优先从 URL 推断真实文件名
_URL_FALLBACK_FILENAMES = {"file.pdf", "file", "document.pdf", "document", "untitled.pdf"}
# markdown 图片引用 ![alt](src) 的正则,与 structured_markdown.IMAGE_PATTERN 一致
_IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class MinerUTool(BuiltinTool):
    """MinerU PDF 解析内置工具（MinerUV3Client 薄包装）。"""

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

    # === 内置配置（租户级,在 builtin_tools.json / 工具管理页填写） ===

    def get_required_config_parameters(self) -> List[str]:
        return ["api_url"]

    # === 执行时参数（运行时覆盖,缺省回落到内置配置 / 工具默认值） ===

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

    # === 参数校验 ===

    def validate_parameters(self, parameters: Dict[str, Any]) -> Dict[str, str]:
        """数值类可选参数在工作流里常见地被模板渲染成空字符串 ''（引用的变量
        未取到值），基类校验里的 ``float('')`` 会直接判为非法，导致节点还没走到
        ``execute``/``_resolve_int_runtime_param`` 的兼容解析就先报错。这里把
        “空字符串”当作“未传”摘除，交给运行时参数解析回落到配置/默认值。
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

    # === 执行入口 ===

    async def execute(self, **kwargs: Any) -> ToolResult:
        """调用 MinerUV3Client 解析单个文件,返回 markdown 字符串。

        Args:
            **kwargs:
                file_name  (str, 推荐 — 用于服务端保存与响应定位;若仅传 file_url 可省略,会从 URL 推断)
                file_url   (str, 可选 — http(s) URL,工具会下载后解析;LLM/Agent 入口)
                file_content (bytes, 可选 — 文档提取器节点直接传入文件二进制,优先级最高)
                其它覆盖参数见 ``parameters``。

        Raises:
            ValueError: 若 ``file_name`` / ``file_url`` / ``file_content`` 均为空。

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

            # ---- sys.files / FileObject 归一化 ----
            # 变量池返回的是 ArrayVariable.value = list[FileVariable]，
            # 每个 FileVariable 内部包一个 FileObject；
            # 单个文件变量返回 FileVariable 实例。
            # 这里统一还原成 string URL + file_name。
            from app.core.workflow.variable.base_variable import FileObject
            from app.core.workflow.variable.variable_objects import FileVariable
            
            def _extract_from_file_var(item) -> tuple[str, str, str]:
                """从 FileVariable / FileObject / dict 中提取 (file_id, url, name)"""
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
                
                # 如果 name 为空，从 file_id 生成一个默认名称
                if not name and fid:
                    name = f"file_{fid}"
                
                return fid, url, name
            
            resolved_file_id: str = ""
            if isinstance(file_url, (FileVariable, FileObject)) or (
                isinstance(file_url, dict) and file_url.get("is_file")
            ):
                # 单个文件变量
                resolved_file_id, file_url_str, name = _extract_from_file_var(file_url)
                if not file_name:
                    file_name = name
                file_url = file_url_str
            elif isinstance(file_url, list) and file_url:
                # 数组文件变量 — 取第一个文件
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

            # 有 file_id 时, 直接从存储后端读文件字节, 绕过 HTTP 下载
            # (sys.files 的 url 指向 localhost, 同步 HTTP 请求自己会超时)
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

            # 验证：必须有 file_name、file_url、file_content 或 resolved_file_id 中的至少一个
            if not (file_name or file_url or file_content or resolved_file_id):
                raise ValueError(
                    "file_name, file_url, or file_content is required"
                )

            # 把 file_url 规整到 file_content,统一交给 _resolve_file_url 处理
            if not file_content and file_url:
                file_content = file_url

            file_name, file_content = self._resolve_file_url(
                file_name, file_content
            )
            
            if not isinstance(file_content, bytes) or not file_content:
                raise RuntimeError(f"[MinerUTool] empty file binary (type={type(file_content).__name__})")

            # 文件类型检查：MinerU 只支持 PDF、图片、DOCX、PPTX、XLSX
            supported_extensions = {
                ".pdf",
                ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".webp",
                ".docx",
                ".pptx",
                ".xlsx",
            }
            ext = Path(file_name).suffix.lower() if file_name else ""
            
            # 无扩展名时,用 magic bytes 识别真实类型
            if not ext and isinstance(file_content, bytes) and len(file_content) >= 8:
                # PDF: %PDF-
                if file_content.startswith(b"%PDF-"):
                    ext = ".pdf"
                    file_name = f"{file_name}.pdf"
                # DOCX/PPTX/XLSX: ZIP 格式,PK 头
                elif file_content.startswith(b"PK\x03\x04"):
                    # 进一步检查 ZIP 内的 content_types 判断具体类型
                    # 这里简化处理,允许所有 Office Open XML 格式
                    ext = ".docx"  # 先假设为 docx,后续会由服务端精确判断
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
            
            # 最终检查:必须有扩展名且在支持列表中
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

            # 运行时参数: 优先级 execute kwargs > builtin configuration > 内置默认
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

            # MinerUV3Client.parse 内部走 /tasks 异步协议 + 轮询,含阻塞 HTTP /
            # 轮询循环,放到默认线程池避免阻塞事件循环。相比 parse_to_markdown,
            # parse 额外返回已解码的图片(MinerUV3Result.images),供下面上传。
            mineru_result = await asyncio.to_thread(
                client.parse,
                file_name,
                file_content,
                int(start_page_id),
                int(end_page_id),
                None,  # callback: 工具场景无 LLM 进度回调需求
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

            # 只返回 markdown,图片 URL 已内嵌到 markdown 的图片引用中
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

    # === 客户端实例化 helpers ===

    def _resolve_api_url(self) -> str:
        """读取并校验 ``api_url`` 配置项;失败抛 ValueError。"""
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

    # === 图片上传 helpers ===

    async def _read_file_from_storage(self, file_id: str) -> bytes:
        """通过 file_id 直接从存储后端读取文件字节, 绕过 HTTP 下载。

        sys.files 的 url 指向 localhost:8000/storage/permanent/{file_id},
        同步 HTTP 请求自己会导致超时。直接查 FileMetadata 拿 file_key,
        再用 FileStorageService.download_file 读存储后端。
        """
        from app.db import get_db_read
        from app.models.file_metadata_model import FileMetadata
        from app.services.file_storage_service import FileStorageService

        file_uuid = uuid.UUID(file_id)
        with get_db_read() as db:
            meta = db.query(FileMetadata).filter(FileMetadata.id == file_uuid).first()
            if not meta:
                raise FileNotFoundError(f"file_id={file_id} not found in file_metadata")
            file_key = meta.file_key

        storage = FileStorageService()
        return await storage.download_file(file_key)

    def _resolve_tenant_id(self) -> Optional[uuid.UUID]:
        """从运行时上下文的 workspace_id 反查 tenant_id（工具本身不持有租户信息）。"""
        workspace_id = self.get_runtime_context("workspace_id")
        if not workspace_id:
            return None
        try:
            workspace_uuid = workspace_id if isinstance(workspace_id, uuid.UUID) else uuid.UUID(str(workspace_id))
        except (TypeError, ValueError):
            return None

        from app.db import get_db_read
        from app.repositories.tool_repository import ToolRepository

        with get_db_read() as db:
            return ToolRepository.get_tenant_id_by_workspace_id(db, str(workspace_uuid))

    async def _store_images_and_rewrite_markdown(
        self, markdown: str, images: Dict[str, MinerUV3Image]
    ) -> tuple[str, List[str]]:
        """把 MinerU 抽出的图片上传到现有存储后端,把 markdown 里的图片引用
        重写为上传后的 URL,并返回去重后的 URL 列表。

        上传依赖 tenant_id/workspace_id;拿不到时跳过上传,markdown 原样返回
        （图片引用仍是 MinerU 服务端的临时文件名，不可访问，但不影响文本内容）。
        """
        tenant_id = self._resolve_tenant_id()
        workspace_id = self.get_runtime_context("workspace_id")
        if tenant_id is None:
            logger.warning(
                "[MinerUTool] skip image upload: cannot resolve tenant_id from "
                "workspace_id=%r", workspace_id,
            )
            return markdown, []

        from app.services.file_storage_service import FileStorageService

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
        # 保序去重
        image_urls = list(dict.fromkeys(url_by_src.values()))
        return rewritten_markdown, image_urls

    @staticmethod
    async def _upload_single_image(
        storage_service: Any,
        tenant_id: uuid.UUID,
        workspace_id: Any,
        mineru_image: MinerUV3Image,
    ) -> str:
        from app.core.config import settings
        from app.services.file_storage_service import generate_file_key

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

        from app.db import get_db_context
        from app.models.file_metadata_model import FileMetadata

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

        server_url = (settings.FILE_LOCAL_SERVER_URL or "").rstrip("/")
        return f"{server_url}/storage/permanent/{file_id}"

    # === 文件下载 helper（agent 场景下把 file_url 转成 bytes） ===

    @staticmethod
    def _resolve_file_url(
        file_name: Any, file_content: Any
    ) -> tuple[Any, Any]:
        """当 ``file_content`` 看起来是 http(s) URL 时,先下载成真实字节。

        这是为 agent/LLM 场景加的便捷层 — 模型只能拿到 URL,无法拿到真实文件
        字节,让它把 URL 透传到 ``file_content`` 即可,工具内部会自己下载。

        节点直接传 ``bytes`` 的原行为完全不受影响。
        """
        if not isinstance(file_content, str):
            return file_name, file_content
        url = file_content.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            # 不是 URL,按原行为返回(下游 MinerUV3Client 拿到非 bytes 会自己报错)
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
                # 预检 Content-Length,避免 LLM 场景下下载过大文件把内存炸了
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

        # 若调用方没传 file_name 或者传的是占位名,尝试从 URL 推断真实文件名
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

    # === 运行时参数解析 ===

    def _resolve_int_runtime_param(
        self, name: str, kwargs: Dict[str, Any], default: int
    ) -> int:
        """读 int 运行时参数,失败回落到 default。

        兼容点:工作流模板渲染 / 表单输入经常把数字传成字符串(如 "0"),
        工具内部按 ``int()`` 自己转换,与 ``tools/base.py._validate_parameter_type``
        行为解耦 — 即便上层校验严格拒绝字符串数字,mineru 工具仍能正常解析。
        真正无法解析的输入(非数字字符串、None 等)会显式抛 ``ValueError``,
        避免被静默回落到 default 后用户拿到意外结果。
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
            # bool 是 int 子类(True=1/False=0),按数字传进来几乎一定是用户
            # 误传,显式拒绝以免静默变成 0/1
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
        """读 bool 运行时参数,失败回落到 default。

        兼容点同 ``_resolve_int_runtime_param``:工作流模板渲染常把布尔传成
        字符串("true"/"false"/"1"/"0"),这里显式识别常见写法。
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

    # === 测试连接 ===

    async def test_connection(self) -> Dict[str, Any]:
        """实际访问 MinerUV3 服务根路径的 /health 验证连接。"""
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