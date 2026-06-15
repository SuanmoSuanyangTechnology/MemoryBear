from app.core.rag.nlp import naive_merge, naive_merge_docx, naive_merge_with_images

from ..context import ChunkContext, MergeResult, ParseResult
from .base import ChunkMerger


class NaiveMerger(ChunkMerger):
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        chunks = naive_merge(
            parse_result.sections,
            int(ctx.parser_config.get("chunk_token_num", 128)),
            ctx.parser_config.get("delimiter", "\n!?。；！？"),
        )
        return MergeResult(chunks=chunks)


class DocxMerger(ChunkMerger):
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        chunks, images = naive_merge_docx(
            parse_result.sections,
            int(ctx.parser_config.get("chunk_token_num", 128)),
            ctx.parser_config.get("delimiter", "\n!?。；！？"),
        )
        return MergeResult(chunks=chunks, images=images)


class ImageMerger(ChunkMerger):
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        chunks, images = naive_merge_with_images(
            parse_result.sections,
            parse_result.section_images,
            int(ctx.parser_config.get("chunk_token_num", 128)),
            ctx.parser_config.get("delimiter", "\n!?。；！？"),
        )
        return MergeResult(chunks=chunks, images=images)
