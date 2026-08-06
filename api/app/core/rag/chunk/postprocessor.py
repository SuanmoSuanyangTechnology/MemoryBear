import copy

from app.core.rag.nlp import add_positions, tokenize

from .context import ChunkContext, LogicalChunk, LogicalChunkType, MergeResult, ParseResult


ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "\u200b\u200c\u200d\ufeff")
ZERO_WIDTH_HTML_ENTITIES = (
    "&ZeroWidthSpace;",
    "&zwj;",
    "&zwnj;",
    "&#8203;",
    "&#8204;",
    "&#8205;",
    "&#65279;",
    "&#x200b;",
    "&#x200B;",
    "&#x200c;",
    "&#x200C;",
    "&#x200d;",
    "&#x200D;",
    "&#xfeff;",
    "&#xFEFF;",
)


class ChunkPostProcessor:
    def process(
        self,
        ctx: ChunkContext,
        parse_result: ParseResult,
        merge_result: MergeResult,
    ) -> list[dict] | tuple[list[dict], list[dict], dict[int, int]]:
        if merge_result.parent_chunks is not None and merge_result.child_chunks is not None:
            child_chunks = self._serialize_chunks(ctx, merge_result.child_chunks, merge_result)
            parent_chunks = self._serialize_chunks(ctx, merge_result.parent_chunks, merge_result)
            return child_chunks, parent_chunks, merge_result.parent_id_map or {}

        logical_chunks = merge_result.logical_chunks
        if logical_chunks is None:
            logical_chunks = [
                LogicalChunk(type=LogicalChunkType.TEXT, content=chunk)
                for chunk in merge_result.chunks
            ]
        return self._serialize_chunks(ctx, logical_chunks, merge_result)

    def _serialize_chunks(
        self,
        ctx: ChunkContext,
        chunks: list[LogicalChunk],
        merge_result: MergeResult,
    ) -> list[dict]:
        result: list[dict] = []
        for index, chunk in enumerate(chunks):
            if chunk.type is LogicalChunkType.TABLE:
                table_chunk = self._serialize_table_chunk(ctx, chunk)
                if table_chunk:
                    result.append(table_chunk)
                continue
            if chunk.type is LogicalChunkType.IMAGE:
                image_chunk = self._serialize_text_chunk(ctx, chunk, merge_result, index)
                if image_chunk:
                    image_chunk["doc_type_kwd"] = "image"
                    result.append(image_chunk)
                continue
            text_chunk = self._serialize_text_chunk(ctx, chunk, merge_result, index)
            if text_chunk:
                result.append(text_chunk)
        return result

    def _serialize_text_chunk(
        self,
        ctx: ChunkContext,
        chunk: LogicalChunk,
        merge_result: MergeResult,
        index: int,
    ) -> dict | None:
        content = self._clean_content(str(chunk.content or ""))
        if not content.strip():
            return None
        doc = copy.deepcopy(ctx.doc)
        pdf_parser = merge_result.pdf_parser
        if pdf_parser:
            try:
                doc["image"], positions = pdf_parser.crop(content, need_position=True)
                add_positions(doc, positions)
                content = self._clean_content(pdf_parser.remove_tag(content))
                if not content.strip():
                    return None
            except NotImplementedError:
                pass
        elif chunk.positions:
            add_positions(doc, chunk.positions)
        else:
            add_positions(doc, [[index] * 5])
        if chunk.image is not None:
            doc["image"] = chunk.image
        metadata = self._chunk_metadata(chunk)
        if metadata:
            doc["metadata"] = metadata
        tokenize(doc, content, ctx.is_english)
        return doc

    def _serialize_table_chunk(self, ctx: ChunkContext, chunk: LogicalChunk) -> dict | None:
        rows = chunk.content
        if not rows:
            return None
        if isinstance(rows, list):
            delimiter = "; " if ctx.is_english else "； "
            content = delimiter.join(str(row) for row in rows if row)
        else:
            content = str(rows)
        content = self._clean_content(content)
        if not content.strip():
            return None
        doc = copy.deepcopy(ctx.doc)
        if chunk.image is not None:
            doc["image"] = chunk.image
            doc["doc_type_kwd"] = "image"
        if chunk.positions:
            add_positions(doc, chunk.positions)
        metadata = self._chunk_metadata(chunk)
        if metadata:
            doc["metadata"] = metadata
        tokenize(doc, content, ctx.is_english)
        doc["content_with_weight"] = content
        return doc

    def _clean_content(self, content: str) -> str:
        content = content.translate(ZERO_WIDTH_TRANSLATION)
        for entity in ZERO_WIDTH_HTML_ENTITIES:
            content = content.replace(entity, "")
        return content

    def _chunk_metadata(self, chunk: LogicalChunk) -> dict:
        metadata = copy.deepcopy(chunk.metadata) if chunk.metadata else {}
        metadata.setdefault("heading_path", [])
        return metadata
