"""Explicit Plain/MinerU/TextLn PDF pipeline with no fallback."""

from __future__ import annotations

from pathlib import Path

from ...parser_config import resolve_layout_recognize
from ..context import ChunkContext, ParseResult
from ..file_utils import extract_links_from_pdf
from ..parser.mineru_v3 import MinerUV3Parser
from ..parser.pdf.plain import PlainPdfParser
from ..parser.pdf.textln import TextLnPdfParser
from .base import ChunkPipeline


class PdfChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        urls: set[str] = set()
        binary = ctx.binary if ctx.binary is not None else Path(ctx.filename).read_bytes()
        if ctx.parser_config.get("analyze_hyperlink", False) and ctx.is_root:
            urls = extract_links_from_pdf(binary)
        self._callback(ctx, 0.1, "Start to parse.")
        layout = resolve_layout_recognize(ctx.parser_config)
        if layout == "plain":
            result = PlainPdfParser().parse(ctx)
        elif layout == "mineru":
            result = MinerUV3Parser().parse(ctx)
        else:
            result = TextLnPdfParser().parse(ctx)
        result.urls = urls
        self._callback(ctx, 0.8, "Finish parsing.")
        return result

    @staticmethod
    def _callback(ctx: ChunkContext, progress: float, message: str) -> None:
        if ctx.callback:
            ctx.callback(progress, message)


__all__ = ["PdfChunkPipeline"]
