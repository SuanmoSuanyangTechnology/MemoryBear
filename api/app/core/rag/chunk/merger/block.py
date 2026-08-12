from copy import deepcopy
from dataclasses import dataclass
from html import escape

from bs4 import BeautifulSoup

from app.core.rag.common.token_utils import encoder, num_tokens_from_string
from app.core.rag.chunk.parser.markdown_preprocessor import (
    ALPHA_LIST_PATTERN,
    CHINESE_LIST_PATTERN,
    CIRCLED_LIST_PATTERN,
    DEFINITION_LIST_PATTERN,
    EMPTY_ORDERED_LIST_PATTERN,
    KEYCAP_LIST_PATTERN,
    ORDERED_LIST_PATTERN,
    ORDINAL_LIST_PATTERN,
    PAREN_ORDERED_LIST_PATTERN,
    PREFIXED_LIST_PATTERN,
    QA_LIST_PATTERN,
    STEP_LIST_PATTERN,
    UNORDERED_LIST_PATTERN,
)
from app.core.rag.chunk.context import (
    ChunkContext,
    ChunkOutputMode,
    LogicalChunk,
    LogicalChunkType,
    MergeResult,
    ParentChildGroup,
    ParsedBlock,
    ParsedBlockType,
    ParseResult,
)
from app.core.rag.chunk.merger.structured_stream import (
    SourceFragment,
    StructuredStream,
    rebuild_structured_stream,
    split_structured_stream,
)

from .base import ChunkMerger
from .text import TextMerger


TEXT_LIKE_TYPES = {
    ParsedBlockType.HEADING,
    ParsedBlockType.TEXT,
    ParsedBlockType.BLOCKQUOTE,
}

LIST_ITEM_START_PATTERNS = (
    UNORDERED_LIST_PATTERN,
    ORDERED_LIST_PATTERN,
    EMPTY_ORDERED_LIST_PATTERN,
    PAREN_ORDERED_LIST_PATTERN,
    KEYCAP_LIST_PATTERN,
    CIRCLED_LIST_PATTERN,
    STEP_LIST_PATTERN,
    ORDINAL_LIST_PATTERN,
    CHINESE_LIST_PATTERN,
    ALPHA_LIST_PATTERN,
    DEFINITION_LIST_PATTERN,
    QA_LIST_PATTERN,
    PREFIXED_LIST_PATTERN,
)

FULL_DOC_MAX_CHARS = 10000
PARENT_CHILD_ATOMIC_TYPES = {
    ParsedBlockType.TABLE,
    ParsedBlockType.IMAGE,
}


@dataclass(frozen=True)
class _StreamUnit:
    fragment: SourceFragment
    separator_before: str = ""


@dataclass
class _ChunkDraft:
    content: str
    fragments: list[SourceFragment]


@dataclass
class _ParentDraft:
    parent: LogicalChunk
    fragments: list[SourceFragment]


class BlockMerger(ChunkMerger):
    def __init__(self):
        self.text_merger = TextMerger()

    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        if parse_result.structured_markdown_stream:
            return self._merge_structured_stream(ctx, parse_result)

        blocks = parse_result.blocks or []
        token_num = int(ctx.parser_config.get("chunk_token_num", 128))
        delimiter = ctx.parser_config.get("delimiter")
        chunk_overlap = _safe_int(ctx.parser_config.get("chunk_overlap", 0))

        if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
            if ctx.parser_config.get("parent_chunk_mode") == "full-doc":
                full_doc_blocks = self._full_doc_blocks(blocks)
                full_text = "\n\n".join(
                    str(block.content)
                    for block in full_doc_blocks
                    if str(block.content or "").strip()
                )
                groups = []
                if full_text:
                    parent = LogicalChunk(type=LogicalChunkType.TEXT, content=full_text)
                    children = self._blocks_to_logical_chunks(
                        full_doc_blocks,
                        token_num,
                        delimiter,
                        0,
                        merge_lists_with_text=parse_result.markdown_preprocess_profile == "mineru",
                        preserve_atomic_blocks=False,
                    )
                    groups.append(ParentChildGroup(parent=parent, children=children))
                return self._parent_child_merge_result(groups, parse_result.pdf_parser)

            parent_token_num = int(ctx.parser_config.get("parent_chunk_token_num", 1024))
            parent_chunk_delimiter = ctx.parser_config.get("parent_chunk_delimiter")
            parent_chunks = self._blocks_to_logical_chunks(
                blocks,
                parent_token_num,
                parent_chunk_delimiter,
                0,
                merge_lists_with_text=parse_result.markdown_preprocess_profile == "mineru",
                preserve_atomic_blocks=True,
            )
            groups = self._build_parent_child_groups(
                parent_chunks,
                token_num,
                delimiter,
                0,
            )
            return self._parent_child_merge_result(groups, parse_result.pdf_parser)

        logical_chunks = self._blocks_to_logical_chunks(
            blocks,
            token_num,
            delimiter,
            chunk_overlap,
            merge_lists_with_text=parse_result.markdown_preprocess_profile == "mineru",
        )
        return MergeResult(
            chunks=self._serialize_chunk_contents(logical_chunks),
            logical_chunks=logical_chunks,
            pdf_parser=parse_result.pdf_parser,
        )

    def _merge_structured_stream(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        token_num = max(int(ctx.parser_config.get("chunk_token_num", 128)), 1)
        delimiter = ctx.parser_config.get("delimiter")
        overlap = TextMerger._normalize_overlap(
            _safe_int(ctx.parser_config.get("chunk_overlap", 0)),
            token_num,
        )
        stream = rebuild_structured_stream(parse_result.blocks or [])
        drafts: list[_ChunkDraft] = []
        for segment in split_structured_stream(stream, delimiter):
            units = self._stream_units(segment, token_num)
            drafts.extend(self._pack_stream_units(units, token_num, overlap))

        logical_chunks = [self._draft_to_normal_chunk(draft) for draft in drafts]
        return MergeResult(
            chunks=self._serialize_chunk_contents(logical_chunks),
            logical_chunks=logical_chunks,
            pdf_parser=parse_result.pdf_parser,
        )

    def _stream_units(self, stream: StructuredStream, token_num: int) -> list[_StreamUnit]:
        units: list[_StreamUnit] = []
        previous_end = 0
        for index, span in enumerate(stream.spans):
            fragment = SourceFragment(
                source_key=span.source_key,
                block=span.block,
                content=stream.text[span.start : span.end],
                complete=stream.text[span.start : span.end] == str(span.block.content or ""),
                structure_valid=(
                    span.block.type is not ParsedBlockType.TABLE
                    or self._is_valid_table_content(stream.text[span.start : span.end])
                ),
            )
            unit = _StreamUnit(
                fragment=fragment,
                separator_before="" if index == 0 else stream.text[previous_end : span.start],
            )
            units.extend(self._split_stream_unit(unit, token_num))
            previous_end = span.end
        return units

    def _split_stream_unit(self, unit: _StreamUnit, token_num: int) -> list[_StreamUnit]:
        content = unit.fragment.content
        if num_tokens_from_string(content) <= token_num:
            return [unit]

        block = unit.fragment.block
        block_type = block.type
        if block_type is ParsedBlockType.IMAGE:
            pieces = self.text_merger.hard_split(content, token_num)
        elif block_type is ParsedBlockType.CODE:
            pieces = self._split_code_content(
                content,
                token_num,
                str(block.metadata.get("language", "")),
            )
        elif block_type is ParsedBlockType.TABLE:
            pieces = self._split_table_content(content, token_num)
        else:
            pieces = self.text_merger.split_recursive(content, token_num)

        total = len(pieces)
        split_units: list[_StreamUnit] = []
        for index, piece in enumerate(pieces):
            piece_block = block
            if block_type is ParsedBlockType.TABLE and total > 1:
                piece_block = deepcopy(block)
                piece_block.metadata["table_part_index"] = index
                piece_block.metadata["table_part_total"] = total
            fragment = SourceFragment(
                source_key=unit.fragment.source_key,
                block=piece_block,
                content=piece,
                complete=False,
                structure_valid=(
                    block_type is not ParsedBlockType.IMAGE
                    and (
                        block_type is not ParsedBlockType.TABLE
                        or self._is_valid_table_content(piece)
                    )
                ),
            )
            separator = unit.separator_before if index == 0 else (
                "\n" if block_type in {ParsedBlockType.CODE, ParsedBlockType.TABLE} else ""
            )
            split_units.append(_StreamUnit(fragment=fragment, separator_before=separator))
        return split_units

    def _pack_stream_units(
        self,
        units: list[_StreamUnit],
        token_num: int,
        overlap: int,
    ) -> list[_ChunkDraft]:
        drafts: list[_ChunkDraft] = []
        current: list[_StreamUnit] = []

        for index, unit in enumerate(units):
            if self._is_incomplete_table_unit(unit):
                if current:
                    drafts.append(self._units_to_draft(current))
                    current = []
                drafts.append(self._units_to_draft([unit]))
                continue

            candidate = [*current, unit]
            if self._heading_should_start_next_draft(current, unit, units, index, token_num):
                drafts.append(self._units_to_draft(current))
                current = [unit]
                continue

            if current and not self._stream_units_within_limit(candidate, token_num):
                drafts.append(self._units_to_draft(current))
                current = self._overlap_units(current, unit, token_num, overlap)

            current.append(unit)

        if current:
            drafts.append(self._units_to_draft(current))
        return drafts

    @staticmethod
    def _is_incomplete_table_unit(unit: _StreamUnit) -> bool:
        return (
            unit.fragment.block.type is ParsedBlockType.TABLE
            and not unit.fragment.complete
        )

    def _draft_to_normal_chunk(self, draft: _ChunkDraft) -> LogicalChunk:
        source_keys = {fragment.source_key for fragment in draft.fragments}
        source_blocks = self._source_blocks(draft.fragments)
        single_source = len(source_keys) == 1
        block = source_blocks[0]

        if single_source and block.type is ParsedBlockType.IMAGE:
            return LogicalChunk(
                type=LogicalChunkType.IMAGE,
                content=draft.content,
                image=block.image,
                positions=deepcopy(block.positions),
                metadata=self._metadata_for_block(block),
                source_image_key=draft.fragments[0].source_key,
                image_tag_complete=(
                    len(draft.fragments) == 1 and draft.fragments[0].complete
                ),
                image_vision_scope=block.image_vision_scope,
            )

        if (
            single_source
            and block.type is ParsedBlockType.TABLE
            and all(fragment.structure_valid for fragment in draft.fragments)
        ):
            return LogicalChunk(
                type=LogicalChunkType.TABLE,
                content=draft.content,
                image=block.image,
                positions=deepcopy(block.positions),
                metadata=self._metadata_for_block(block),
            )

        if single_source and block.type is ParsedBlockType.CODE:
            return LogicalChunk(
                type=LogicalChunkType.TEXT,
                content=draft.content,
                image=block.image,
                positions=deepcopy(block.positions),
                metadata=self._metadata_for_block(block),
            )

        metadata = self._metadata_for_range(source_blocks, "text")
        metadata.pop("vision_text", None)
        metadata.pop("image", None)
        return LogicalChunk(
            type=LogicalChunkType.TEXT,
            content=draft.content,
            metadata=metadata,
        )

    def _heading_should_start_next_draft(
        self,
        current: list[_StreamUnit],
        unit: _StreamUnit,
        units: list[_StreamUnit],
        index: int,
        token_num: int,
    ) -> bool:
        if not current or unit.fragment.block.type is not ParsedBlockType.HEADING:
            return False
        if index + 1 >= len(units):
            return False
        if not self._stream_units_within_limit([*current, unit], token_num):
            return False
        return not self._stream_units_within_limit([*current, unit, units[index + 1]], token_num)

    def _overlap_units(
        self,
        completed: list[_StreamUnit],
        next_unit: _StreamUnit,
        token_num: int,
        overlap: int,
    ) -> list[_StreamUnit]:
        if overlap <= 0:
            return []

        retained: list[_StreamUnit] = []
        for unit in reversed(completed):
            candidate = [unit, *retained]
            if num_tokens_from_string(self._join_stream_units(candidate)) > overlap:
                break
            if not self._stream_units_within_limit([*candidate, next_unit], token_num):
                break
            retained = candidate
        return retained

    def _units_to_draft(self, units: list[_StreamUnit]) -> _ChunkDraft:
        return _ChunkDraft(
            content=self._join_stream_units(units),
            fragments=[unit.fragment for unit in units],
        )

    @staticmethod
    def _join_stream_units(units: list[_StreamUnit]) -> str:
        return "".join(
            unit.fragment.content
            if index == 0
            else f"{unit.separator_before}{unit.fragment.content}"
            for index, unit in enumerate(units)
        )

    def _stream_units_within_limit(self, units: list[_StreamUnit], token_num: int) -> bool:
        return num_tokens_from_string(self._join_stream_units(units)) <= token_num

    @staticmethod
    def _source_blocks(fragments: list[SourceFragment]) -> list[ParsedBlock]:
        blocks: list[ParsedBlock] = []
        seen: set[int] = set()
        for fragment in fragments:
            if fragment.source_key in seen:
                continue
            seen.add(fragment.source_key)
            blocks.append(fragment.block)
        return blocks

    def _full_doc_blocks(self, blocks: list[ParsedBlock]) -> list[ParsedBlock]:
        selected: list[ParsedBlock] = []
        content_length = 0

        for block in blocks:
            content = str(block.content or "")
            if not content.strip():
                continue

            separator_length = 2 if selected else 0
            remaining = FULL_DOC_MAX_CHARS - content_length - separator_length
            if remaining <= 0:
                break

            if len(content) <= remaining:
                selected.append(block)
                content_length += separator_length + len(content)
                continue

            if block.type in PARENT_CHILD_ATOMIC_TYPES:
                break

            truncated = deepcopy(block)
            truncated.content = content[:remaining]
            selected.append(truncated)
            break

        return selected

    def _blocks_to_logical_chunks(
        self,
        blocks: list[ParsedBlock],
        token_num: int,
        delimiter: str | None,
        overlap: int,
        *,
        merge_lists_with_text: bool = False,
        preserve_atomic_blocks: bool = False,
    ) -> list[LogicalChunk]:
        logical_chunks: list[LogicalChunk] = []
        text_group: list[ParsedBlock] = []
        text_like_types = set(TEXT_LIKE_TYPES)
        if merge_lists_with_text:
            text_like_types.add(ParsedBlockType.LIST)

        def flush_text_group():
            if not text_group:
                return
            metadata = self._metadata_for_range(text_group, "text")
            content = "\n\n".join(str(block.content) for block in text_group if str(block.content).strip())
            chunks = (
                [content]
                if delimiter is None and num_tokens_from_string(content) <= token_num
                else self.text_merger.merge(content, token_num, delimiter, overlap)
            )
            for chunk in chunks:
                logical_chunks.append(
                    LogicalChunk(
                        type=LogicalChunkType.TEXT,
                        content=chunk,
                        metadata=deepcopy(metadata),
                    )
                )
            text_group.clear()

        for block in blocks:
            if block.type in text_like_types:
                text_group.append(block)
                continue

            flush_text_group()

            if block.type is ParsedBlockType.LIST:
                logical_chunks.extend(self._list_logical_chunks(block, token_num, delimiter, overlap))
                continue

            if block.type is ParsedBlockType.CODE:
                logical_chunks.extend(self._code_logical_chunks(block, token_num))
                continue

            if block.type is ParsedBlockType.TABLE:
                if preserve_atomic_blocks:
                    logical_chunks.append(
                        LogicalChunk(
                            type=LogicalChunkType.TABLE,
                            content=block.content,
                            image=block.image,
                            positions=deepcopy(block.positions),
                            metadata=self._metadata_for_block(block),
                        )
                    )
                else:
                    logical_chunks.extend(self._table_logical_chunks(block, token_num))
                continue

            if block.type is ParsedBlockType.IMAGE:
                logical_chunks.append(
                    LogicalChunk(
                        type=LogicalChunkType.IMAGE,
                        content=block.content,
                        image=block.image,
                        positions=deepcopy(block.positions),
                        metadata=self._metadata_for_block(block),
                    )
                )

        flush_text_group()
        return logical_chunks

    def _build_parent_child_groups(
        self,
        parent_chunks: list[LogicalChunk],
        child_token_num: int,
        delimiter: str | None,
        overlap: int,
    ) -> list[ParentChildGroup]:
        return [
            ParentChildGroup(
                parent=parent,
                children=self._split_parent_chunk(parent, child_token_num, delimiter, overlap),
            )
            for parent in parent_chunks
        ]

    def _parent_child_merge_result(
        self,
        groups: list[ParentChildGroup],
        pdf_parser,
    ) -> MergeResult:
        parent_chunks: list[LogicalChunk] = []
        child_chunks: list[LogicalChunk] = []
        parent_id_map: dict[int, int] = {}

        for group in groups:
            parent_index = len(parent_chunks)
            parent_chunks.append(group.parent)
            for child in group.children:
                parent_id_map[len(child_chunks)] = parent_index
                child_chunks.append(child)

        return MergeResult(
            chunks=self._serialize_chunk_contents(child_chunks),
            logical_chunks=child_chunks,
            parent_child_groups=groups,
            parent_chunks=parent_chunks,
            child_chunks=child_chunks,
            parent_id_map=parent_id_map,
            pdf_parser=pdf_parser,
        )

    def _split_parent_chunk(
        self,
        parent: LogicalChunk,
        token_num: int,
        delimiter: str | None,
        overlap: int,
    ) -> list[LogicalChunk]:
        if parent.type is LogicalChunkType.TABLE:
            return self._split_logical_table_chunk(parent, token_num)

        if parent.type is LogicalChunkType.IMAGE:
            return [deepcopy(parent)]

        block_type = parent.metadata.get("block_type")
        if block_type == ParsedBlockType.LIST.value:
            return [
                LogicalChunk(
                    type=LogicalChunkType.TEXT,
                    content=chunk,
                    image=parent.image,
                    positions=deepcopy(parent.positions),
                    metadata=deepcopy(parent.metadata),
                )
                for chunk in self._split_list_content(str(parent.content), token_num, delimiter, overlap)
                if chunk.strip()
            ]

        if block_type == ParsedBlockType.CODE.value:
            return [
                LogicalChunk(
                    type=LogicalChunkType.TEXT,
                    content=chunk,
                    image=parent.image,
                    positions=deepcopy(parent.positions),
                    metadata=deepcopy(parent.metadata),
                )
                for chunk in self._split_code_content(
                    str(parent.content),
                    token_num,
                    parent.metadata.get("language", ""),
                )
                if chunk.strip()
            ]

        return [
            LogicalChunk(
                type=LogicalChunkType.TEXT,
                content=chunk,
                image=parent.image,
                positions=deepcopy(parent.positions),
                metadata=deepcopy(parent.metadata),
            )
            for chunk in self.text_merger.merge(str(parent.content), token_num, delimiter, overlap)
            if chunk.strip()
        ]

    def _list_logical_chunks(
        self,
        block: ParsedBlock,
        token_num: int,
        delimiter: str | None,
        overlap: int,
    ) -> list[LogicalChunk]:
        metadata = self._metadata_for_block(block)
        return [
            LogicalChunk(
                type=LogicalChunkType.TEXT,
                content=content,
                positions=deepcopy(block.positions),
                metadata=deepcopy(metadata),
            )
            for content in self._split_list_content(str(block.content), token_num, delimiter, overlap)
            if content.strip()
        ]

    def _split_list_content(
        self,
        content: str,
        token_num: int,
        delimiter: str | None = None,
        overlap: int = 0,
    ) -> list[str]:
        limit = max(int(token_num), 1)
        overlap_tokens = TextMerger._normalize_overlap(overlap, limit)
        if num_tokens_from_string(content) <= limit:
            return [content]

        items = self._split_list_items(content)
        if len(items) <= 1:
            return self.text_merger.merge(content, limit, delimiter, overlap)

        chunks: list[str] = []
        current_items: list[str] = []
        for item in items:
            if num_tokens_from_string(item) > limit:
                if current_items:
                    chunks.append(self._join_list_items(current_items))
                    current_items = []
                chunks.extend(self.text_merger.merge(item, limit, delimiter, overlap))
                continue

            candidate_items = [*current_items, item]
            candidate = self._join_list_items(candidate_items)
            if current_items and num_tokens_from_string(candidate) > limit:
                chunks.append(self._join_list_items(current_items))
                current_items = self._retain_list_overlap_items(
                    current_items,
                    item,
                    limit,
                    overlap_tokens,
                )

            current_items.append(item)

        if current_items:
            chunks.append(self._join_list_items(current_items))
        return chunks or [content]

    def _split_list_items(self, content: str) -> list[str]:
        items: list[str] = []
        current_lines: list[str] = []
        for line in content.split("\n"):
            starts_new_item = self._is_list_item_start(line)
            if starts_new_item and current_lines:
                items.append("\n".join(current_lines).rstrip())
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            items.append("\n".join(current_lines).rstrip())
        return [item for item in items if item.strip()]

    def _is_list_item_start(self, line: str) -> bool:
        if not line.strip() or line.startswith((" ", "\t")):
            return False
        return any(pattern.match(line) for pattern in LIST_ITEM_START_PATTERNS)

    def _retain_list_overlap_items(
        self,
        current_items: list[str],
        next_item: str,
        limit: int,
        overlap: int,
    ) -> list[str]:
        retained = current_items[:]
        while retained and (
            num_tokens_from_string(self._join_list_items(retained)) > overlap
            or num_tokens_from_string(self._join_list_items([*retained, next_item])) > limit
        ):
            retained = retained[1:]
        return retained

    @staticmethod
    def _join_list_items(items: list[str]) -> str:
        return "\n".join(items)

    def _code_logical_chunks(self, block: ParsedBlock, token_num: int) -> list[LogicalChunk]:
        metadata = self._metadata_for_block(block)
        language = str(block.metadata.get("language", ""))
        return [
            LogicalChunk(
                type=LogicalChunkType.TEXT,
                content=chunk,
                positions=deepcopy(block.positions),
                metadata=deepcopy(metadata),
            )
            for chunk in self._split_code_content(str(block.content), token_num, language)
            if chunk.strip()
        ]

    def _table_logical_chunks(self, block: ParsedBlock, token_num: int) -> list[LogicalChunk]:
        metadata = self._metadata_for_block(block)
        contents = self._split_table_content(str(block.content), token_num)
        total = len(contents)
        result: list[LogicalChunk] = []
        for index, content in enumerate(contents):
            chunk_metadata = deepcopy(metadata)
            if total > 1:
                chunk_metadata["table_part_index"] = index
                chunk_metadata["table_part_total"] = total
            result.append(
                LogicalChunk(
                    type=LogicalChunkType.TABLE,
                    content=content,
                    image=block.image,
                    positions=deepcopy(block.positions),
                    metadata=chunk_metadata,
                )
            )
        return result

    def _split_logical_table_chunk(self, chunk: LogicalChunk, token_num: int) -> list[LogicalChunk]:
        contents = self._split_table_content(str(chunk.content), token_num)
        if len(contents) == 1:
            return [deepcopy(chunk)]

        result: list[LogicalChunk] = []
        total = len(contents)
        for index, content in enumerate(contents):
            metadata = deepcopy(chunk.metadata)
            metadata["table_part_index"] = index
            metadata["table_part_total"] = total
            result.append(
                LogicalChunk(
                    type=LogicalChunkType.TABLE,
                    content=content,
                    image=chunk.image,
                    positions=deepcopy(chunk.positions),
                    metadata=metadata,
                )
            )
        return result

    def _split_table_content(self, content: str, token_num: int) -> list[str]:
        if num_tokens_from_string(content) <= token_num:
            return [content]

        table = BeautifulSoup(content, "html.parser").find("table")
        if table is None:
            return self._hard_split_wrapped(content, token_num, lambda piece: piece)

        header_html, row_htmls = self._extract_table_parts(table)
        if not row_htmls:
            return self._hard_split_wrapped(content, token_num, lambda piece: piece)

        chunks: list[str] = []
        current_rows: list[str] = []

        for row_html in row_htmls:
            single_row_table = self._build_table_html(header_html, [row_html])
            if num_tokens_from_string(single_row_table) > token_num:
                if current_rows:
                    chunks.append(self._build_table_html(header_html, current_rows))
                    current_rows = []
                chunks.extend(self._split_oversized_table_row(header_html, row_html, token_num))
                continue

            candidate_rows = [*current_rows, row_html]
            candidate = self._build_table_html(header_html, candidate_rows)
            if current_rows and num_tokens_from_string(candidate) > token_num:
                chunks.append(self._build_table_html(header_html, current_rows))
                current_rows = [row_html]
            else:
                current_rows = candidate_rows

        if current_rows:
            chunks.append(self._build_table_html(header_html, current_rows))

        return chunks or [content]

    @staticmethod
    def _is_valid_table_content(content: str) -> bool:
        stripped = content.strip()
        if not stripped.startswith("<table") or not stripped.endswith("</table>"):
            return False
        return BeautifulSoup(stripped, "html.parser").find("table") is not None

    def _extract_table_parts(self, table) -> tuple[str, list[str]]:
        thead = table.find("thead")
        if thead is not None:
            header_html = str(thead)
            rows = [
                str(row)
                for row in table.find_all("tr")
                if row.find_parent("thead") is None
            ]
            return header_html, rows

        rows = table.find_all("tr")
        if not rows:
            return "", []
        header_html = f"<thead>{rows[0]}</thead>"
        return header_html, [str(row) for row in rows[1:]]

    def _build_table_html(self, header_html: str, row_htmls: list[str]) -> str:
        body = "".join(row_htmls)
        if header_html:
            return f"<table>{header_html}<tbody>{body}</tbody></table>"
        return f"<table><tbody>{body}</tbody></table>"

    def _split_oversized_table_row(self, header_html: str, row_html: str, token_num: int) -> list[str]:
        row = BeautifulSoup(row_html, "html.parser").find("tr")
        row_text = row.get_text(" | ", strip=True) if row is not None else row_html

        def wrap(piece: str) -> str:
            row_fragment = f"<tr><td>{escape(piece)}</td></tr>"
            return self._build_table_html(header_html, [row_fragment])

        return self._hard_split_wrapped(row_text, token_num, wrap)

    def _split_code_content(self, content: str, token_num: int, language: str = "") -> list[str]:
        if num_tokens_from_string(content) <= token_num:
            return [content]

        body_lines = content.split("\n")
        fence_start = ""
        fence_end = ""
        if body_lines and body_lines[0].strip().startswith("```"):
            fence_start = body_lines[0]
            if body_lines[-1].strip().startswith("```"):
                fence_end = body_lines[-1]
                body_lines = body_lines[1:-1]
            else:
                body_lines = body_lines[1:]
        elif language:
            fence_start = f"```{language}"
            fence_end = "```"

        chunks: list[str] = []
        current_lines: list[str] = []

        for line in body_lines:
            if num_tokens_from_string(self._wrap_code_lines([line], fence_start, fence_end)) > token_num:
                if current_lines:
                    chunks.append(self._wrap_code_lines(current_lines, fence_start, fence_end))
                    current_lines = []
                chunks.extend(self._split_oversized_code_line(line, token_num, fence_start, fence_end))
                continue

            candidate_lines = current_lines + [line]
            candidate = self._wrap_code_lines(candidate_lines, fence_start, fence_end)
            if current_lines and num_tokens_from_string(candidate) > token_num:
                chunks.append(self._wrap_code_lines(current_lines, fence_start, fence_end))
                current_lines = [line]
            else:
                current_lines = candidate_lines

        if current_lines:
            chunks.append(self._wrap_code_lines(current_lines, fence_start, fence_end))

        return chunks or [content]

    def _split_oversized_code_line(
        self,
        line: str,
        token_num: int,
        fence_start: str,
        fence_end: str,
    ) -> list[str]:
        return self._hard_split_wrapped(
            line,
            token_num,
            lambda piece: self._wrap_code_lines([piece], fence_start, fence_end),
        )

    def _hard_split_wrapped(self, text: str, token_num: int, wrap) -> list[str]:
        tokens = encoder.encode(text)
        limit = max(int(token_num), 1)
        if num_tokens_from_string(wrap("")) > limit:
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {limit}."
            )
        chunks: list[str] = []
        index = 0
        while index < len(tokens):
            boundary = self._find_wrapped_token_boundary(tokens, index, limit, wrap)
            if boundary is None:
                raise ValueError(
                    f"Structured content cannot fit within token limit {limit}."
                )

            end, piece = boundary
            chunks.append(wrap(piece))
            index = end
        return chunks

    def _find_wrapped_token_boundary(
        self,
        tokens: list[int],
        start: int,
        limit: int,
        wrap,
    ) -> tuple[int, str] | None:
        search_end = min(len(tokens), start + limit)
        for end in range(search_end, start, -1):
            piece = self._decode_token_slice(tokens, start, end)
            if piece is not None and num_tokens_from_string(wrap(piece)) <= limit:
                return end, piece
        return None

    @staticmethod
    def _decode_token_slice(tokens: list[int], start: int, end: int) -> str | None:
        try:
            return encoder.decode(tokens[start:end], errors="strict")
        except UnicodeDecodeError:
            return None

    def _wrap_code_lines(self, lines: list[str], fence_start: str, fence_end: str) -> str:
        if not fence_start:
            return "\n".join(lines)
        end = fence_end or "```"
        return "\n".join([fence_start, *lines, end])

    def _metadata_for_block(self, block: ParsedBlock) -> dict:
        metadata = deepcopy(block.metadata)
        metadata.update(
            {
                "block_type": block.type.value,
                "block_seq_start": block.seq,
                "block_seq_end": block.seq,
                "start_line": block.start_line,
                "end_line": block.end_line,
            }
        )
        return metadata

    def _metadata_for_range(self, blocks: list[ParsedBlock], block_type: str) -> dict:
        first = blocks[0]
        last = blocks[-1]
        metadata = {
            "block_type": block_type,
            "block_count": len(blocks),
            "block_seq_start": first.seq,
            "block_seq_end": last.seq,
            "start_line": first.start_line,
            "end_line": last.end_line,
        }
        self._add_range_context_metadata(metadata, blocks)
        return metadata

    def _add_range_context_metadata(self, metadata: dict, blocks: list[ParsedBlock]) -> None:
        heading_paths = _unique_paths(
            block.metadata.get("heading_path", [])
            for block in blocks
            if isinstance(block.metadata, dict)
        )
        if len(heading_paths) == 1:
            metadata["heading_path"] = heading_paths[0]
        elif len(heading_paths) > 1:
            metadata["heading_paths"] = heading_paths

        if len(blocks) == 1 and blocks[0].type is ParsedBlockType.LIST:
            for key in ("list_item_count", "list_markers", "list_marker_kinds", "contains_qa_marker"):
                if key in blocks[0].metadata:
                    metadata[key] = deepcopy(blocks[0].metadata[key])

    def _serialize_chunk_contents(self, chunks: list[LogicalChunk]) -> list[str]:
        return [str(chunk.content) for chunk in chunks if str(chunk.content or "").strip()]


def _unique_paths(paths) -> list[list[str]]:
    result: list[list[str]] = []
    seen = set()
    for path in paths:
        if not isinstance(path, list):
            continue
        key = tuple(str(item) for item in path)
        if key in seen:
            continue
        seen.add(key)
        result.append(list(key))
    return result


def _safe_int(value, default: int = 0) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default
