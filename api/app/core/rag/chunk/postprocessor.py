from app.core.rag.nlp import tokenize_chunks, tokenize_chunks_with_images, tokenize_table

from .context import ChunkContext, MergeResult, ParseResult


class ChunkPostProcessor:
    def process(
        self,
        ctx: ChunkContext,
        parse_result: ParseResult,
        merge_result: MergeResult,
    ) -> list[dict]:
        result = tokenize_table(parse_result.tables or [], ctx.doc, ctx.is_english)
        if parse_result.merge_strategy in ["docx", "with_images"] and merge_result.images is not None:
            result.extend(
                tokenize_chunks_with_images(
                    merge_result.chunks,
                    ctx.doc,
                    ctx.is_english,
                    merge_result.images,
                )
            )
        else:
            result.extend(
                tokenize_chunks(
                    merge_result.chunks,
                    ctx.doc,
                    ctx.is_english,
                    parse_result.pdf_parser,
                )
            )
        return result
