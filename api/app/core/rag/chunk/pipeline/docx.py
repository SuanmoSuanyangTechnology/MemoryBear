import logging

from app.core.rag.deepdoc.parser.figure_parser import vision_figure_parser_docx_wrapper
from app.core.rag.chunk.parser.docx import DocxParser
from app.core.rag.utils.file_utils import extract_html, extract_links_from_docx

from app.core.rag.chunk.context import ChunkContext, ParseResult
from .base import ChunkPipeline


class DocxChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        url_res = self.collect_docx_hyperlink_chunks(ctx)

        sections, tables = DocxParser().parse(ctx)
        tables = vision_figure_parser_docx_wrapper(
            sections=sections,
            tbls=tables,
            callback=ctx.callback,
            vision_model=ctx.vision_model,
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
