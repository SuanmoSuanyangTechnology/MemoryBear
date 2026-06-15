from app.core.rag.chunk.context import ChunkContext, ParseResult
from app.core.rag.chunk.parser.excel import ExcelParser
from app.core.rag.nlp import tokenize_chunks

from .base import ChunkPipeline


class ExcelChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        ctx.callback(0.1, "Start to parse.")
        sections = ExcelParser().parse(ctx)
        ctx.callback(0.8, "Finish parsing.")
        chunks = [section for section, _ in sections]
        direct_result = tokenize_chunks(chunks, ctx.doc, ctx.is_english, None)
        return ParseResult(direct_result=direct_result)
