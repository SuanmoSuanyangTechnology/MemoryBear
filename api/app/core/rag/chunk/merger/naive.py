from app.core.rag.nlp import naive_merge, naive_merge_docx, naive_merge_with_images

from ..context import ChunkContext, ChunkOutputMode, LogicalChunk, LogicalChunkType, MergeResult, ParseResult
from ..parent_child import build_parent_child_logical_chunks
from .base import ChunkMerger


def _text_logical_chunks(chunks: list[str], images: list | None = None) -> list[LogicalChunk]:
    if images is None:
        images = [None] * len(chunks)
    return [
        LogicalChunk(type=LogicalChunkType.TEXT, content=chunk, image=image)
        for chunk, image in zip(chunks, images)
        if isinstance(chunk, str) and chunk.strip()
    ]


def _table_logical_chunks(tables: list | None) -> list[LogicalChunk]:
    result: list[LogicalChunk] = []
    for table in tables or []:
        image = None
        rows = table
        positions = None
        if (
            isinstance(table, tuple)
            and len(table) == 2
            and isinstance(table[0], tuple)
            and len(table[0]) == 2
        ):
            (image, rows), positions = table
        if not rows:
            continue
        result.append(
            LogicalChunk(
                type=LogicalChunkType.TABLE,
                content=rows,
                image=image,
                positions=positions,
            )
        )
    return result


def _split_text(text: str, token_num: int, delimiter: str) -> list[str]:
    return naive_merge([(text, "")], token_num, delimiter)


def _legacy_chunks(logical_chunks: list[LogicalChunk]) -> list[str]:
    return [str(chunk.content) for chunk in logical_chunks if chunk.type is LogicalChunkType.TEXT]

# normal text merger
class NaiveMerger(ChunkMerger):
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        token_num = int(ctx.parser_config.get("chunk_token_num", 128))
        delimiter = ctx.parser_config.get("delimiter", "\n!?。；！？")
        chunks = naive_merge(
            parse_result.sections,
            token_num,
            delimiter,
        )
        logical_chunks = _text_logical_chunks(chunks) + _table_logical_chunks(parse_result.tables)
        result = MergeResult(chunks=chunks, logical_chunks=logical_chunks, pdf_parser=parse_result.pdf_parser)
        if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
            parent_token_num = int(ctx.parser_config.get("parent_chunk_token_num", 1024))
            parent_texts = naive_merge(parse_result.sections, parent_token_num, delimiter)
            parent_chunks = _text_logical_chunks(parent_texts) + _table_logical_chunks(parse_result.tables)
            child_chunks, parent_chunks, parent_id_map = build_parent_child_logical_chunks(
                parent_chunks,
                ctx.parser_config,
                lambda text, size: _split_text(text, size, delimiter),
            )
            result.parent_chunks = parent_chunks
            result.child_chunks = child_chunks
            result.parent_id_map = parent_id_map
            result.chunks = _legacy_chunks(child_chunks)
        return result


# docx merger
class DocxMerger(ChunkMerger):
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        token_num = int(ctx.parser_config.get("chunk_token_num", 128))
        delimiter = ctx.parser_config.get("delimiter", "\n!?。；！？")
        chunks, images = naive_merge_docx(
            parse_result.sections,
            token_num,
            delimiter,
        )
        logical_chunks = _text_logical_chunks(chunks, images) + _table_logical_chunks(parse_result.tables)
        result = MergeResult(chunks=chunks, images=images, logical_chunks=logical_chunks, pdf_parser=parse_result.pdf_parser)
        if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
            parent_token_num = int(ctx.parser_config.get("parent_chunk_token_num", 1024))
            parent_texts, parent_images = naive_merge_docx(parse_result.sections, parent_token_num, delimiter)
            parent_chunks = _text_logical_chunks(parent_texts, parent_images) + _table_logical_chunks(parse_result.tables)
            child_chunks, parent_chunks, parent_id_map = build_parent_child_logical_chunks(
                parent_chunks,
                ctx.parser_config,
                lambda text, size: _split_text(text, size, delimiter),
            )
            result.parent_chunks = parent_chunks
            result.child_chunks = child_chunks
            result.parent_id_map = parent_id_map
            result.chunks = _legacy_chunks(child_chunks)
            result.images = [chunk.image for chunk in child_chunks if chunk.type is LogicalChunkType.TEXT]
        return result


# with image merger
class ImageMerger(ChunkMerger):
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        token_num = int(ctx.parser_config.get("chunk_token_num", 128))
        delimiter = ctx.parser_config.get("delimiter", "\n!?。；！？")
        chunks, images = naive_merge_with_images(
            parse_result.sections,
            parse_result.section_images,
            token_num,
            delimiter,
        )
        logical_chunks = _text_logical_chunks(chunks, images) + _table_logical_chunks(parse_result.tables)
        result = MergeResult(chunks=chunks, images=images, logical_chunks=logical_chunks, pdf_parser=parse_result.pdf_parser)
        if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
            parent_token_num = int(ctx.parser_config.get("parent_chunk_token_num", 1024))
            parent_texts, parent_images = naive_merge_with_images(
                parse_result.sections,
                parse_result.section_images,
                parent_token_num,
                delimiter,
            )
            parent_chunks = _text_logical_chunks(parent_texts, parent_images) + _table_logical_chunks(parse_result.tables)
            child_chunks, parent_chunks, parent_id_map = build_parent_child_logical_chunks(
                parent_chunks,
                ctx.parser_config,
                lambda text, size: _split_text(text, size, delimiter),
            )
            result.parent_chunks = parent_chunks
            result.child_chunks = child_chunks
            result.parent_id_map = parent_id_map
            result.chunks = _legacy_chunks(child_chunks)
            result.images = [chunk.image for chunk in child_chunks if chunk.type is LogicalChunkType.TEXT]
        return result
