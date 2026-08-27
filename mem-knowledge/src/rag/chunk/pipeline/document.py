"""DOCX, PPTX, and legacy PPT pipelines without parser fallback."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from ....bootstrap import get_settings
from ..context import ChunkContext, ParseResult
from ..file_utils import extract_html, extract_links_from_docx
from ..libreoffice import convert_to_pdf
from ..parser.mineru_v3 import MinerUV3Parser
from ..preprocessor import safe_log_target
from .base import ChunkPipeline

LOGGER = logging.getLogger(__name__)


class DocxChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        self._callback(ctx, 0.1, "Start to parse.")
        url_res = self.collect_docx_hyperlink_chunks(ctx)
        result = MinerUV3Parser().parse(ctx)
        result.url_res = url_res
        self._callback(ctx, 0.8, "Finish parsing.")
        return result

    def collect_docx_hyperlink_chunks(self, ctx: ChunkContext) -> list:
        if not ctx.parser_config.get("analyze_hyperlink", False) or not ctx.is_root:
            return []
        binary = ctx.binary if ctx.binary is not None else Path(ctx.filename).read_bytes()
        chunks = []
        for index, url in enumerate(extract_links_from_docx(binary)):
            html_bytes, _metadata = extract_html(url)
            if not html_bytes:
                continue
            try:
                child = self.run_child(url, binary=html_bytes, ctx=ctx, is_root=False)
            except Exception as exc:  # noqa: BLE001 - preserve legacy HTML fallback naming.
                LOGGER.info(
                    "DOCX hyperlink child used local HTML name target=%s error_type=%s",
                    safe_log_target(url),
                    type(exc).__name__,
                )
                child = self.run_child(
                    f"{index}.html",
                    binary=html_bytes,
                    ctx=ctx,
                    is_root=False,
                )
            chunks.extend(child or [])
        return chunks

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


class PresentationChunkPipeline(ChunkPipeline):
    START_PROGRESS = 0.1
    CONVERT_PROGRESS = 0.2
    CHILD_PROGRESS_START = 0.3
    CHILD_PROGRESS_END = 0.75
    FINISH_PROGRESS = 0.8

    def parse(self, ctx: ChunkContext) -> ParseResult:
        self._callback(ctx, self.START_PROGRESS, "Start to parse.")
        if Path(ctx.filename).suffix.lower() == ".pptx":
            result = MinerUV3Parser().parse(ctx)
        else:
            self._callback(ctx, self.CONVERT_PROGRESS, "Convert presentation to PDF.")
            result = self._parse_converted_pdf(ctx)
        self._callback(ctx, self.FINISH_PROGRESS, "Finish parsing.")
        return result

    def _parse_converted_pdf(self, ctx: ChunkContext) -> ParseResult:
        source_path: str | None = None
        converted_path: str | None = None
        try:
            suffix = Path(ctx.filename).suffix or ".ppt"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as source:
                source_path = source.name
                source.write(
                    ctx.binary if ctx.binary is not None else Path(ctx.filename).read_bytes()
                )
            converted_path = convert_to_pdf(source_path)
            converted_binary = Path(converted_path).read_bytes()
            result = self.run_child(
                converted_path,
                binary=converted_binary,
                ctx=self._converted_pdf_context(ctx),
                is_root=ctx.is_root,
                vision_model=ctx.vision_model,
            )
            return ParseResult(direct_result=result, append_embed=False)
        finally:
            for path in {source_path, converted_path}:
                if path and os.path.exists(path):
                    os.unlink(path)

    def _converted_pdf_context(self, ctx: ChunkContext) -> ChunkContext:
        if not ctx.callback:
            return ctx

        def callback(*args, **kwargs):
            progress = args[0] if args else kwargs.get("prog", kwargs.get("progress"))
            message = args[1] if len(args) > 1 else kwargs.get("msg", kwargs.get("message"))
            ctx.callback(self._map_progress(progress), message)

        return replace(ctx, callback=callback)

    def _map_progress(self, progress):
        if not isinstance(progress, (int, float)) or progress < 0:
            return progress
        ratio = (progress - self.START_PROGRESS) / (
            self.FINISH_PROGRESS - self.START_PROGRESS
        )
        ratio = min(max(ratio, 0), 1)
        return self.CHILD_PROGRESS_START + ratio * (
            self.CHILD_PROGRESS_END - self.CHILD_PROGRESS_START
        )

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


class LegacyDocChunkPipeline(ChunkPipeline):
    """Parse legacy binary Word documents through the pinned Tika server jar."""

    def parse(self, ctx: ChunkContext) -> ParseResult:
        self._callback(ctx, 0.1, "Start to parse.")
        try:
            import tika

            os.environ["TIKA_SERVER_JAR"] = get_settings().tika_server_jar
            tika.initVM()
            from tika import parser as tika_parser
        except Exception as exc:  # noqa: BLE001 - optional runtime initialization is fallible.
            LOGGER.warning("Tika unavailable error_type=%s", type(exc).__name__)
            self._callback(ctx, 0.8, "Tika is unavailable; legacy DOC returned no chunks.")
            return ParseResult(direct_result=[], append_embed=False)

        temporary_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as temporary:
                temporary_path = temporary.name
                temporary.write(
                    ctx.binary if ctx.binary is not None else Path(ctx.filename).read_bytes()
                )
            parsed = tika_parser.from_file(temporary_path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

        content = parsed.get("content") if isinstance(parsed, dict) else None
        if not content:
            LOGGER.warning("Tika returned empty legacy DOC content")
            self._callback(ctx, 0.8, "Tika returned empty content.")
            return ParseResult(direct_result=[], append_embed=False)
        sections = [(line, "") for line in str(content).splitlines() if line]
        self._callback(ctx, 0.8, "Finish parsing.")
        return ParseResult(sections=sections)

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


__all__ = [
    "DocxChunkPipeline",
    "LegacyDocChunkPipeline",
    "PresentationChunkPipeline",
]
