import logging
import os
import tempfile
from dataclasses import replace

from app.core.rag.chunk.context import ChunkContext, ParseResult
from app.core.rag.chunk.parser.mineru_v3 import MinerUV3Parser
from app.core.rag.chunk.parser.pdf import DeepDocPdfParser, MinerUPdfParser, PlainPdfParser, TextLnPdfParser
from app.core.rag.utils.file_utils import extract_links_from_pdf
from app.core.rag.utils.libre_office import async_convert_to_pdf

from .base import ChunkPipeline


LOGGER = logging.getLogger(__name__)


class PdfChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        urls = set()
        layout_recognizer = ctx.parser_config.get("layout_recognize", "DeepDOC")
        if ctx.parser_config.get("analyze_hyperlink", False) and ctx.is_root:
            urls = extract_links_from_pdf(ctx.binary)

        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        name = layout_recognizer.strip().lower()
        ctx.callback(0.1, "Start to parse.")

        if name == "mineru":
            try:
                parse_result = MinerUV3Parser().parse(ctx)
                parse_result.urls = urls
                ctx.callback(0.8, "Finish parsing.")
                return parse_result
            except Exception as exc:
                LOGGER.warning(
                    "[MinerUV3] parse failed, fallback started: file_name=%s, fallback=old_mineru, error=%s",
                    ctx.filename,
                    exc,
                )
                ctx.callback(0.78, "MinerU V3 failed, fallback to old flow.")

        parser = self.select_parser(name)
        try:
            sections, tables, pdf_parser = parser.parse(ctx)
        except Exception as exc:
            if name == "mineru":
                LOGGER.error(
                    "[MinerUV3] fallback failed: file_name=%s, fallback=old_mineru, error=%s",
                    ctx.filename,
                    exc,
                )
            raise

        if not sections and not tables:
            return ParseResult(direct_result=[], append_embed=False)

        if name in ["mineru", "textln"] and not ctx.kwargs.get("_keep_chunk_token_num"):
            ctx.parser_config["chunk_token_num"] = 0

        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(sections=sections, tables=tables, pdf_parser=pdf_parser, urls=urls)

    def select_parser(self, name):
        if name == "deepdoc":
            return DeepDocPdfParser()
        if name == "mineru":
            return MinerUPdfParser()
        if name == "textln":
            return TextLnPdfParser()
        return PlainPdfParser()


class PresentationChunkPipeline(ChunkPipeline):
    START_PROGRESS = 0.1
    CONVERT_PROGRESS = 0.2
    CHILD_PROGRESS_START = 0.3
    CHILD_PROGRESS_END = 0.75
    FINISH_PROGRESS = 0.8

    def parse(self, ctx: ChunkContext) -> ParseResult:
        self._callback(ctx, self.START_PROGRESS, "Start to parse.")
        if self._file_extension(ctx) == ".pptx":
            try:
                parse_result = MinerUV3Parser().parse(ctx)
                self._callback(ctx, self.FINISH_PROGRESS, "Finish parsing.")
                return parse_result
            except Exception as exc:
                LOGGER.warning(
                    "[MinerUV3] presentation parse failed, fallback started: file_name=%s, fallback=converted_pdf, error=%s",
                    ctx.filename,
                    exc,
                )
                self._callback(ctx, self.CONVERT_PROGRESS, "MinerU V3 failed, convert presentation to PDF.")
        else:
            self._callback(ctx, self.CONVERT_PROGRESS, "Convert presentation to PDF.")

        parse_result = self._parse_converted_pdf(ctx)
        self._callback(ctx, self.FINISH_PROGRESS, "Finish parsing.")
        return parse_result

    def _file_extension(self, ctx: ChunkContext) -> str:
        return os.path.splitext(ctx.filename)[1].lower()

    def _callback(self, ctx: ChunkContext, progress, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)

    def _converted_pdf_context(self, ctx: ChunkContext) -> ChunkContext:
        if not ctx.callback:
            return ctx

        def converted_pdf_callback(progress=None, message=None):
            mapped_progress = self._map_converted_pdf_progress(progress)
            ctx.callback(mapped_progress, message)

        return replace(ctx, callback=converted_pdf_callback)

    def _map_converted_pdf_progress(self, progress):
        if not isinstance(progress, (int, float)):
            return progress

        child_start = self.START_PROGRESS
        child_end = self.FINISH_PROGRESS
        ratio = (progress - child_start) / (child_end - child_start)
        ratio = min(max(ratio, 0), 1)
        return self.CHILD_PROGRESS_START + ratio * (self.CHILD_PROGRESS_END - self.CHILD_PROGRESS_START)

    def _parse_converted_pdf(self, ctx: ChunkContext) -> ParseResult:
        tmp_file = None
        dest_pdf_path = None
        try:
            suffix = os.path.splitext(ctx.filename)[1] or ".pptx"
            tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            if ctx.binary:
                tmp_file.write(ctx.binary)
            else:
                with open(ctx.filename, "rb") as file:
                    tmp_file.write(file.read())
            tmp_file.close()

            future = async_convert_to_pdf(tmp_file.name)
            dest_pdf_path = future.result()
            child_ctx = self._converted_pdf_context(ctx)
            direct_result = self.run_child(
                dest_pdf_path,
                binary=None,
                ctx=child_ctx,
                is_root=ctx.kwargs.get("is_root", True),
                vision_model=ctx.vision_model,
            )
            return ParseResult(direct_result=direct_result, append_embed=False)
        finally:
            if tmp_file and os.path.exists(tmp_file.name):
                os.unlink(tmp_file.name)
            if dest_pdf_path and os.path.exists(dest_pdf_path):
                os.unlink(dest_pdf_path)
