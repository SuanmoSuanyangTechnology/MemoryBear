import copy

from .context import (
    ChunkContext,
    LogicalChunk,
    LogicalChunkType,
    MergeResult,
    ParentChildGroup,
    ParseResult,
)
from .hierarchy import GroupedChildChunks, validate_parent_child_result
from .tokenization import add_positions, set_chunk_content

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
        if merge_result.parent_child_groups is not None:
            return self._serialize_parent_child_groups(ctx, merge_result)

        if merge_result.parent_chunks is not None and merge_result.child_chunks is not None:
            groups = self._build_mapped_parent_child_groups(ctx, merge_result)
            return self._serialize_parent_child_groups(ctx, merge_result, groups)

        logical_chunks = merge_result.logical_chunks
        if logical_chunks is None:
            logical_chunks = [
                LogicalChunk(type=LogicalChunkType.TEXT, content=chunk)
                for chunk in merge_result.chunks
            ]
        return self._serialize_chunks(ctx, logical_chunks, merge_result)

    def _serialize_parent_child_groups(
        self,
        ctx: ChunkContext,
        merge_result: MergeResult,
        groups: list[ParentChildGroup] | None = None,
    ) -> tuple[list[dict], list[dict], dict[int, int]]:
        child_chunks = GroupedChildChunks()
        parent_chunks: list[dict] = []
        parent_id_map: dict[int, int] = {}
        mode = str(ctx.parser_config.get("parent_chunk_mode") or "paragraph")
        source_groups = groups if groups is not None else merge_result.parent_child_groups or []

        for group_index, group in enumerate(source_groups):
            parent_chunk = self._serialize_chunk(
                ctx, group.parent, merge_result, len(parent_chunks)
            )
            if parent_chunk is None:
                has_serializable_child = any(
                    self._serialize_chunk(
                        ctx,
                        child,
                        merge_result,
                        len(child_chunks) + child_offset,
                    )
                    is not None
                    for child_offset, child in enumerate(group.children)
                )
                if not has_serializable_child:
                    continue
                raise ValueError(
                    f"Invalid {mode} hierarchy: parent group {group_index} "
                    "was removed during serialization."
                )

            serialized_children: list[dict] = []
            for child in group.children:
                serialized_child = self._serialize_chunk(
                    ctx,
                    child,
                    merge_result,
                    len(child_chunks) + len(serialized_children),
                )
                if serialized_child is not None:
                    serialized_children.append(serialized_child)

            if not serialized_children:
                raise ValueError(
                    f"Invalid {mode} hierarchy: parent group {group_index} "
                    "has no serializable children."
                )

            parent_index = len(parent_chunks)
            parent_chunks.append(parent_chunk)
            for child in serialized_children:
                parent_id_map[len(child_chunks)] = parent_index
                child_chunks.append(child)

        validate_parent_child_result(child_chunks, parent_chunks, parent_id_map, mode)
        return child_chunks, parent_chunks, parent_id_map

    def _build_mapped_parent_child_groups(
        self,
        ctx: ChunkContext,
        merge_result: MergeResult,
    ) -> list[ParentChildGroup]:
        parent_chunks = merge_result.parent_chunks or []
        child_chunks = merge_result.child_chunks or []
        parent_id_map = merge_result.parent_id_map or {}
        mode = str(ctx.parser_config.get("parent_chunk_mode") or "paragraph")
        validate_parent_child_result(child_chunks, parent_chunks, parent_id_map, mode)

        groups = [ParentChildGroup(parent=parent) for parent in parent_chunks]
        for child_index, child in enumerate(child_chunks):
            groups[parent_id_map[child_index]].children.append(child)
        return groups

    def _serialize_chunks(
        self,
        ctx: ChunkContext,
        chunks: list[LogicalChunk],
        merge_result: MergeResult,
    ) -> list[dict]:
        result: list[dict] = []
        for index, chunk in enumerate(chunks):
            serialized_chunk = self._serialize_chunk(ctx, chunk, merge_result, index)
            if serialized_chunk:
                result.append(serialized_chunk)
        return result

    def _serialize_chunk(
        self,
        ctx: ChunkContext,
        chunk: LogicalChunk,
        merge_result: MergeResult,
        index: int,
    ) -> dict | None:
        if chunk.type is LogicalChunkType.TABLE:
            return self._serialize_table_chunk(ctx, chunk)
        serialized_chunk = self._serialize_text_chunk(ctx, chunk, merge_result, index)
        if serialized_chunk and chunk.type is LogicalChunkType.IMAGE:
            serialized_chunk["doc_type_kwd"] = "image"
        return serialized_chunk

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
        set_chunk_content(doc, content)
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
        set_chunk_content(doc, content)
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
