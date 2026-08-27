from ..context import ChunkContext, ParseResult
from ..parser.excel import StructuredExcelParser
from .base import ChunkPipeline


class ExcelChunkPipeline(ChunkPipeline):
    def parse(self, ctx: ChunkContext) -> ParseResult:
        if ctx.callback:
            ctx.callback(0.1, "Start to parse.")

        direct_result = StructuredExcelParser().parse(ctx)

        if ctx.callback:
            ctx.callback(0.8, "Finish parsing.")
        return ParseResult(direct_result=direct_result)
