import logging
from typing import Any

from app.core.rag.chunk.context import ChunkContext, ParseResult
from app.core.rag.chunk.parser.excel import ExcelParser, StructuredExcelParser
from app.core.rag.nlp import tokenize_chunks

from .base import ChunkPipeline


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() == "true"


def _is_false(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if value is None:
        return False
    return str(value).strip().lower() == "false"


class ExcelChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        if ctx.callback:
            ctx.callback(0.1, "Start to parse.")

        parser_config = ctx.parser_config or {}
        excel_config = parser_config.get("excel") if isinstance(parser_config.get("excel"), dict) else {}

        if _is_truthy(parser_config.get("html4excel")) or _is_false(excel_config.get("structured")):
            direct_result = self._parse_legacy(ctx)
            if ctx.callback:
                ctx.callback(0.8, "Finish parsing.")
            return ParseResult(direct_result=direct_result)

        try:
            direct_result = StructuredExcelParser().parse(ctx)
        except Exception as exc:
            if _is_false(excel_config.get("fallback_to_legacy")):
                raise
            logging.warning(f"Structured Excel parsing failed, fallback to legacy parser: {exc}", exc_info=True)
            if ctx.callback:
                ctx.callback(0.2, "Structured Excel parsing failed, fallback to legacy parser.")
            direct_result = self._parse_legacy(ctx)

        if ctx.callback:
            ctx.callback(0.8, "Finish parsing.")
        return ParseResult(direct_result=direct_result)

    def _parse_legacy(self, ctx: ChunkContext) -> list[dict]:
        sections = ExcelParser().parse(ctx)
        chunks = [section for section, _ in sections]
        return tokenize_chunks(chunks, ctx.doc, ctx.is_english, None)
