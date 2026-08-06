import logging

from app.core.rag.deepdoc.parser.figure_parser import vision_figure_parser_docx_wrapper
from app.core.rag.chunk.parser.docx import DocxParser
from app.core.rag.chunk.parser.mineru_v3 import MinerUV3Parser
from app.core.rag.utils.file_utils import extract_html, extract_links_from_docx

from app.core.rag.chunk.context import ChunkContext, ParseResult, is_image_vision_enabled
from .base import ChunkPipeline


LOGGER = logging.getLogger(__name__)


class DocxChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        url_res = self.collect_docx_hyperlink_chunks(ctx)

        layout_recognizer = ctx.parser_config.get("layout_recognize", "DeepDOC")
        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        if layout_recognizer.strip().lower() == "mineru":
            try:
                parse_result = MinerUV3Parser().parse(ctx)
                parse_result.url_res = url_res
                ctx.callback(0.8, "Finish parsing.")
                return parse_result
            except Exception as exc:
                LOGGER.warning(
                    "[MinerUV3] parse failed, fallback started: file_name=%s, fallback=docx, error=%s",
                    ctx.filename,
                    exc,
                )
                ctx.callback(0.78, "MinerU V3 failed, fallback to old flow.")

        sections, tables = DocxParser().parse(ctx)
        tables = vision_figure_parser_docx_wrapper(
            sections=sections,
            tbls=tables,
            callback=ctx.callback,
            vision_model=ctx.vision_model if is_image_vision_enabled(ctx.parser_config) else None,
            **ctx.kwargs,
        )
        ctx.callback(0.8, "Finish parsing.")
        return ParseResult(
            sections=sections,
            tables=tables,
            merge_strategy="docx",
            url_res=url_res,
        )

    def collect_docx_hyperlink_chunks(self, ctx: ChunkContext) -> list:
        url_res = []
        if not ctx.parser_config.get("analyze_hyperlink", False) or not ctx.is_root:
            return url_res

        urls = extract_links_from_docx(ctx.binary)
        for index, url in enumerate(urls):
            html_bytes, _ = extract_html(url)
            if not html_bytes:
                continue
            try:
                sub_url_res = self.run_child(
                    url,
                    binary=html_bytes,
                    ctx=ctx,
                    is_root=False,
                    vision_model=ctx.vision_model,
                )
            except Exception as exc:
                logging.info(f"Failed to chunk url in registered file type {url}: {exc}")
                sub_url_res = self.run_child(
                    f"{index}.html",
                    binary=html_bytes,
                    ctx=ctx,
                    is_root=False,
                    vision_model=ctx.vision_model,
                )
            url_res.extend(sub_url_res)
        return url_res
