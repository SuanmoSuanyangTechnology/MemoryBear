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

import re
from typing import List

from ..base import ToolParameter, ParameterType
from .base import BuiltinTool


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
