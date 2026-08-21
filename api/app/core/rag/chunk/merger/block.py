from copy import deepcopy
from dataclasses import dataclass
from html import escape

from bs4 import BeautifulSoup

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
    BlockSpan,
    SourceFragment,
    StructuredStream,
    block_separator,
    rebuild_structured_stream,
    split_structured_stream,
)
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
from app.core.rag.common.token_utils import encoder, num_tokens_from_string

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


@dataclass
class _ChildDraft:
    chunk: LogicalChunk
    source_key: int | None = None


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
        if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
            if ctx.parser_config.get("parent_chunk_mode") == "full-doc":
                parent_drafts = self._build_full_doc_parent_drafts(
                    parse_result.blocks or []
                )
            else:
                parent_token_num = max(
                    int(ctx.parser_config.get("parent_chunk_token_num", 1024)),
                    1,
                )
                parent_drafts = self._build_parent_drafts(
                    parse_result.blocks or [],
                    parent_token_num,
                    ctx.parser_config.get("parent_chunk_delimiter"),
                )
            groups = self._parent_child_groups_from_drafts(
                parent_drafts,
                token_num,
                delimiter,
            )
            return self._parent_child_merge_result(groups, parse_result.pdf_parser)

        overlap = TextMerger._normalize_overlap(
            _safe_int(ctx.parser_config.get("chunk_overlap", 0)),
            token_num,
        )
        stream = rebuild_structured_stream(parse_result.blocks or [])
        drafts = self._segment_drafts(
            stream,
            token_num,
            delimiter,
            overlap,
        )

        logical_chunks = [self._draft_to_normal_chunk(draft) for draft in drafts]
        return MergeResult(
            chunks=self._serialize_chunk_contents(logical_chunks),
            logical_chunks=logical_chunks,
            pdf_parser=parse_result.pdf_parser,
        )

    def _build_parent_drafts(
        self,
        blocks: list[ParsedBlock],
        token_num: int,
        delimiter: str | None,
    ) -> list[_ParentDraft]:
        stream = rebuild_structured_stream(blocks)
        drafts = self._segment_drafts(stream, token_num, delimiter, 0)
        return [self._chunk_draft_to_parent(draft) for draft in drafts]

    def _segment_drafts(
        self,
        stream: StructuredStream,
        token_num: int,
        delimiter: str | None,
        overlap: int,
    ) -> list[_ChunkDraft]:
        drafts: list[_ChunkDraft] = []
        for segment in split_structured_stream(stream, delimiter):
            source_units = self._source_stream_units(segment)
            if not source_units:
                continue
            if self._stream_units_within_limit(source_units, token_num):
                drafts.append(self._units_to_draft(source_units))
                continue
            units = self._stream_units(segment, token_num)
            drafts.extend(self._pack_stream_units(units, token_num, overlap))
        return drafts

    def _build_full_doc_parent_drafts(
        self,
        blocks: list[ParsedBlock],
    ) -> list[_ParentDraft]:
        fragments: list[SourceFragment] = []
        content_parts: list[str] = []
        previous: ParsedBlock | None = None
        content_length = 0

        for source_key, block in enumerate(blocks):
            content = str(block.content or "")
            if not content.strip():
                continue

            separator = block_separator(previous, block) if previous is not None else ""
            remaining = FULL_DOC_MAX_CHARS - content_length - len(separator)
            if remaining <= 0:
                break

            complete = len(content) <= remaining
            if not complete and block.type in PARENT_CHILD_ATOMIC_TYPES:
                break

            selected_content = content if complete else content[:remaining]
            content_parts.extend([separator, selected_content])
            fragments.append(
                SourceFragment(
                    source_key=source_key,
                    block=block,
                    content=selected_content,
                    complete=complete,
                    structure_valid=(
                        block.type is not ParsedBlockType.TABLE
                        or self._is_valid_table_content(selected_content)
                    ),
                )
            )
            content_length += len(separator) + len(selected_content)
            previous = block
            if not complete:
                break

        if not fragments:
            return []

        source_blocks = self._source_blocks(fragments)
        parent = LogicalChunk(
            type=LogicalChunkType.TEXT,
            content="".join(content_parts),
            metadata=self._metadata_for_range(source_blocks, "text"),
        )
        return [_ParentDraft(parent=parent, fragments=fragments)]

    def _chunk_draft_to_parent(self, draft: _ChunkDraft) -> _ParentDraft:
        source_blocks = self._source_blocks(draft.fragments)
        return _ParentDraft(
            parent=LogicalChunk(
                type=LogicalChunkType.TEXT,
                content=draft.content,
                metadata=self._metadata_for_range(source_blocks, "text"),
            ),
            fragments=draft.fragments,
        )

    def _parent_child_groups_from_drafts(
        self,
        drafts: list[_ParentDraft],
        token_num: int,
        delimiter: str | None,
    ) -> list[ParentChildGroup]:
        group_drafts: list[tuple[_ParentDraft, list[_ChildDraft]]] = []
        all_child_drafts: list[_ChildDraft] = []
        for draft in drafts:
            child_drafts = [
                child_draft
                for child_draft in self._child_drafts_from_parent(
                    draft,
                    token_num,
                    delimiter,
                )
                if self._is_serializable_child(child_draft.chunk)
            ]
            if not child_drafts:
                continue
            group_drafts.append((draft, child_drafts))
            all_child_drafts.extend(child_drafts)

        self._normalize_table_child_parts(all_child_drafts)
        return [
            ParentChildGroup(
                parent=draft.parent,
                children=[child_draft.chunk for child_draft in child_drafts],
            )
            for draft, child_drafts in group_drafts
        ]

    def _child_drafts_from_parent(
        self,
        draft: _ParentDraft,
        token_num: int,
        delimiter: str | None,
    ) -> list[_ChildDraft]:
        children: list[_ChildDraft] = []
        text_fragments: list[SourceFragment] = []

        def flush_text_fragments() -> None:
            if not text_fragments:
                return
            children.extend(
                _ChildDraft(chunk=child)
                for child in self._text_children_from_fragments(
                    text_fragments, token_num, delimiter
                )
            )
            text_fragments.clear()

        text_like_types = {*TEXT_LIKE_TYPES, ParsedBlockType.LIST}
        for fragment in draft.fragments:
            if fragment.block.type in text_like_types:
                text_fragments.append(fragment)
                continue

            flush_text_fragments()
            children.extend(
                _ChildDraft(chunk=child, source_key=fragment.source_key)
                for child in self._specialized_children_from_fragment(
                    fragment,
                    token_num,
                )
            )

        flush_text_fragments()
        return children

    @staticmethod
    def _is_serializable_child(child: LogicalChunk) -> bool:
        return bool(str(child.content or "").strip())

    @staticmethod
    def _normalize_table_child_parts(child_drafts: list[_ChildDraft]) -> None:
        parts_by_source: dict[int, list[LogicalChunk]] = {}
        for child_draft in child_drafts:
            if (
                child_draft.source_key is None
                or child_draft.chunk.type is not LogicalChunkType.TABLE
            ):
                continue
            parts_by_source.setdefault(child_draft.source_key, []).append(
                child_draft.chunk
            )

        for parts in parts_by_source.values():
            if len(parts) == 1:
                parts[0].metadata.pop("table_part_index", None)
                parts[0].metadata.pop("table_part_total", None)
                continue
            for index, part in enumerate(parts):
                part.metadata["table_part_index"] = index
                part.metadata["table_part_total"] = len(parts)

    def _text_children_from_fragments(
        self,
        fragments: list[SourceFragment],
        token_num: int,
        delimiter: str | None,
    ) -> list[LogicalChunk]:
        stream = self._text_stream_from_fragments(fragments)
        drafts = self._segment_drafts(stream, token_num, delimiter, 0)
        return [self._draft_to_normal_chunk(draft) for draft in drafts]

    @staticmethod
    def _text_stream_from_fragments(
        fragments: list[SourceFragment],
    ) -> StructuredStream:
        parts: list[str] = []
        spans: list[BlockSpan] = []
        previous: SourceFragment | None = None
        offset = 0
        for fragment in fragments:
            separator = ""
            if previous is not None and previous.source_key != fragment.source_key:
                separator = block_separator(previous.block, fragment.block)
            parts.extend([separator, fragment.content])
            offset += len(separator)
            start = offset
            offset += len(fragment.content)
            spans.append(
                BlockSpan(
                    source_key=fragment.source_key,
                    block=fragment.block,
                    start=start,
                    end=offset,
                )
            )
            previous = fragment
        return StructuredStream(text="".join(parts), spans=spans)

    def _specialized_children_from_fragment(
        self,
        fragment: SourceFragment,
        token_num: int,
    ) -> list[LogicalChunk]:
        if fragment.block.type is ParsedBlockType.CODE:
            return self._code_children_from_fragment(fragment, token_num)

        units = self._split_stream_unit(
            _StreamUnit(fragment=fragment),
            token_num,
        )
        return [
            self._draft_to_normal_chunk(self._units_to_draft([unit])) for unit in units
        ]

    def _code_children_from_fragment(
        self,
        fragment: SourceFragment,
        token_num: int,
    ) -> list[LogicalChunk]:
        block = fragment.block
        language = str(block.metadata.get("language", ""))
        content = self._complete_code_fence(fragment.content, language)
        metadata = self._metadata_for_block(block)
        pieces = self._split_code_content(
            content,
            token_num,
            language,
            strict=True,
        )
        if any(num_tokens_from_string(piece) > token_num for piece in pieces):
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {token_num}."
            )
        return [
            LogicalChunk(
                type=LogicalChunkType.TEXT,
                content=piece,
                image=block.image,
                positions=deepcopy(block.positions),
                metadata=deepcopy(metadata),
            )
            for piece in pieces
            if piece.strip()
        ]

    @staticmethod
    def _complete_code_fence(content: str, language: str) -> str:
        lines = content.split("\n")
        if lines and lines[0].strip().startswith("```"):
            if len(lines) == 1 or not lines[-1].strip().startswith("```"):
                return f"{content}\n```"
            return content
        if language:
            return f"```{language}\n{content}\n```"
        return content

    def _stream_units(self, stream: StructuredStream, token_num: int) -> list[_StreamUnit]:
        units: list[_StreamUnit] = []
        for source_unit in self._source_stream_units(stream):
            units.extend(self._split_stream_unit(source_unit, token_num))
        return units

    def _source_stream_units(self, stream: StructuredStream) -> list[_StreamUnit]:
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
            units.append(unit)
            previous_end = span.end
        return units

    def _split_stream_unit(self, unit: _StreamUnit, token_num: int) -> list[_StreamUnit]:
        content = unit.fragment.content
        block = unit.fragment.block
        block_type = block.type
        if block_type not in {
            ParsedBlockType.IMAGE,
            ParsedBlockType.CODE,
            ParsedBlockType.TABLE,
        }:
            text_units = self.text_merger.split_recursive_units(
                content,
                token_num,
                split_top_level=True,
            )
            if len(text_units) == 1 and text_units[0].text == content:
                return [unit]

            return [
                _StreamUnit(
                    fragment=SourceFragment(
                        source_key=unit.fragment.source_key,
                        block=block,
                        content=text_unit.text,
                        complete=False,
                        structure_valid=True,
                    ),
                    separator_before=(
                        unit.separator_before if index == 0 else text_unit.prefix
                    ),
                )
                for index, text_unit in enumerate(text_units)
            ]

        if num_tokens_from_string(content) <= token_num:
            return [unit]

        if block_type is ParsedBlockType.IMAGE:
            pieces = self.text_merger.hard_split(content, token_num)
        elif block_type is ParsedBlockType.CODE:
            pieces = self._split_code_content(
                content,
                token_num,
                str(block.metadata.get("language", "")),
                strict=True,
            )
        elif block_type is ParsedBlockType.TABLE:
            pieces = self._split_table_content(content, token_num, strict=True)

        total = len(pieces)
        if any(num_tokens_from_string(piece) > token_num for piece in pieces):
            raise ValueError(
                f"Structured content cannot fit within token limit {token_num}."
            )
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

        for unit in units:
            candidate = [*current, unit]
            if current and not self._stream_units_within_limit(candidate, token_num):
                drafts.append(self._units_to_draft(current))
                current = self._overlap_units(current, unit, token_num, overlap)

            current.append(unit)

        if current:
            drafts.append(self._units_to_draft(current))
        return drafts

    def _draft_to_normal_chunk(self, draft: _ChunkDraft) -> LogicalChunk:
        source_keys = {fragment.source_key for fragment in draft.fragments}
        source_blocks = self._source_blocks(draft.fragments)
        single_source = len(source_keys) == 1
        block = source_blocks[0]

        if single_source and block.type is ParsedBlockType.IMAGE:
            image_tag_complete = (
                len(draft.fragments) == 1 and draft.fragments[0].complete
            )
            metadata = self._metadata_for_block(block)
            if not image_tag_complete:
                metadata.pop("vision_text", None)
            return LogicalChunk(
                type=LogicalChunkType.IMAGE,
                content=draft.content,
                image=block.image,
                positions=deepcopy(block.positions),
                metadata=metadata,
                source_image_key=draft.fragments[0].source_key,
                image_tag_complete=image_tag_complete,
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
            fragments=self._draft_fragments(units),
        )

    @staticmethod
    def _draft_fragments(units: list[_StreamUnit]) -> list[SourceFragment]:
        fragments: list[SourceFragment] = []
        for unit in units:
            fragment = unit.fragment
            if (
                fragments
                and fragments[-1].source_key == fragment.source_key
                and fragment.block.type
                in {*TEXT_LIKE_TYPES, ParsedBlockType.LIST}
            ):
                previous = fragments[-1]
                content = f"{previous.content}{unit.separator_before}{fragment.content}"
                fragments[-1] = SourceFragment(
                    source_key=previous.source_key,
                    block=previous.block,
                    content=content,
                    complete=content == str(previous.block.content or ""),
                    structure_valid=(
                        previous.structure_valid and fragment.structure_valid
                    ),
                )
                continue
            fragments.append(fragment)
        return fragments

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

    def _split_table_content(
        self,
        content: str,
        token_num: int,
        *,
        strict: bool = False,
    ) -> list[str]:
        if num_tokens_from_string(content) <= token_num:
            return [content]

        table = BeautifulSoup(content, "html.parser").find("table")
        if table is None:
            return self._hard_split_wrapped(
                content,
                token_num,
                lambda piece: piece,
                strict=strict,
            )

        if strict:
            return self._split_strict_table(table, token_num)

        header_html, row_htmls = self._extract_table_parts(table)
        if not row_htmls:
            return self._hard_split_wrapped(
                content,
                token_num,
                lambda piece: piece,
                strict=strict,
            )

        chunks = self._split_table_rows(
            header_html,
            row_htmls,
            token_num,
            strict=False,
        )
        return chunks or [content]

    def _split_table_rows(
        self,
        header_html: str,
        row_htmls: list[str],
        token_num: int,
        *,
        strict: bool,
    ) -> list[str]:

        chunks: list[str] = []
        current_rows: list[str] = []

        for row_html in row_htmls:
            single_row_table = self._build_table_html(header_html, [row_html])
            if num_tokens_from_string(single_row_table) > token_num:
                if current_rows:
                    chunks.append(self._build_table_html(header_html, current_rows))
                    current_rows = []
                chunks.extend(
                    self._split_oversized_table_row(
                        header_html,
                        row_html,
                        token_num,
                        strict=strict,
                    )
                )
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

        return chunks

    def _split_strict_table(self, table, token_num: int) -> list[str]:
        thead = table.find("thead")
        if not table.find_all("tr"):
            table_text = table.get_text(" ", strip=True)
            if table_text:
                container = self._rowless_table_container(table)

                def wrap_rowless(piece: str) -> str:
                    payload = escape(piece)
                    if container:
                        payload = f"<{container}>{payload}</{container}>"
                    return f"<table>{payload}</table>"

                return self._hard_split_wrapped(
                    table_text,
                    token_num,
                    wrap_rowless,
                    strict=True,
                )

            section = next(
                (
                    name
                    for name in ("thead", "tbody", "tfoot")
                    if table.find(name) is not None
                ),
                None,
            )
            empty_table = (
                f"<table><{section}></{section}></table>"
                if section
                else "<table></table>"
            )
            if num_tokens_from_string(empty_table) <= token_num:
                return [empty_table]
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {token_num}."
            )

        if thead is not None:
            header_html = str(thead)
            body_rows = [
                row
                for row in table.find_all("tr")
                if row.find_parent("thead") is None
            ]
            if body_rows and num_tokens_from_string(
                self._build_table_html(
                    header_html,
                    ["<tr><td></td></tr>"],
                )
            ) <= token_num:
                return self._split_table_rows(
                    header_html,
                    [str(row) for row in body_rows],
                    token_num,
                    strict=True,
                )
            section_rows = [
                ("thead", row) for row in thead.find_all("tr")
            ]
            section_rows.extend(("tbody", row) for row in body_rows)
        else:
            rows = table.find_all("tr")
            if len(rows) > 1:
                header_html = f"<thead>{rows[0]}</thead>"
                if num_tokens_from_string(
                    self._build_table_html(
                        header_html,
                        ["<tr><td></td></tr>"],
                    )
                ) <= token_num:
                    return self._split_table_rows(
                        header_html,
                        [str(row) for row in rows[1:]],
                        token_num,
                        strict=True,
                    )
                section_rows = [
                    ("thead", rows[0]),
                    *(("tbody", row) for row in rows[1:]),
                ]
            else:
                section_rows = [("tbody", row) for row in rows]

        chunks: list[str] = []
        for section, row in section_rows:
            wrapped_row = self._build_section_table_html(section, str(row))
            if num_tokens_from_string(wrapped_row) <= token_num:
                chunks.append(wrapped_row)
            else:
                chunks.extend(
                    self._split_strict_table_row(
                        row,
                        section,
                        token_num,
                    )
                )

        if not chunks or any(
            not self._is_valid_table_content(chunk)
            or num_tokens_from_string(chunk) > token_num
            for chunk in chunks
        ):
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {token_num}."
            )
        return chunks

    @staticmethod
    def _rowless_table_container(table) -> str | None:
        direct_text = "".join(
            str(child).strip()
            for child in table.children
            if getattr(child, "name", None) is None
        )
        containers = [
            child
            for child in table.find_all(
                ["caption", "thead", "tbody", "tfoot"],
                recursive=False,
            )
            if child.get_text(" ", strip=True)
        ]
        if direct_text or len(containers) != 1:
            return None
        return str(containers[0].name)

    def _split_strict_table_row(
        self,
        row,
        section: str,
        token_num: int,
    ) -> list[str]:
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {token_num}."
            )

        chunks: list[str] = []
        for cell in cells:
            cell_name = str(cell.name or "td").lower()
            cell_text = cell.get_text(" ", strip=True)

            def wrap_cell(piece: str, cell_name: str = cell_name) -> str:
                row_fragment = (
                    f"<tr><{cell_name}>{escape(piece)}</{cell_name}></tr>"
                )
                return self._build_section_table_html(section, row_fragment)

            chunks.extend(
                self._hard_split_wrapped(
                    cell_text,
                    token_num,
                    wrap_cell,
                    strict=True,
                )
            )
        return chunks

    @staticmethod
    def _build_section_table_html(section: str, row_html: str) -> str:
        return f"<table><{section}>{row_html}</{section}></table>"

    @staticmethod
    def _is_valid_table_content(content: str) -> bool:
        stripped = content.strip()
        normalized = stripped.lower()
        if not normalized.startswith("<table") or not normalized.endswith("</table>"):
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

    def _split_oversized_table_row(
        self,
        header_html: str,
        row_html: str,
        token_num: int,
        *,
        strict: bool = False,
    ) -> list[str]:
        row = BeautifulSoup(row_html, "html.parser").find("tr")
        if strict and row is not None:
            cells = row.find_all(["th", "td"], recursive=False)
            if cells:
                chunks: list[str] = []
                for cell in cells:
                    cell_name = str(cell.name or "td").lower()
                    cell_text = cell.get_text(" ", strip=True)

                    def wrap_cell(
                        piece: str,
                        cell_name: str = cell_name,
                    ) -> str:
                        row_fragment = (
                            f"<tr><{cell_name}>{escape(piece)}</{cell_name}></tr>"
                        )
                        return self._build_table_html(header_html, [row_fragment])

                    chunks.extend(
                        self._hard_split_wrapped(
                            cell_text,
                            token_num,
                            wrap_cell,
                            strict=True,
                        )
                    )
                return chunks

        row_text = row.get_text(" | ", strip=True) if row is not None else row_html

        def wrap(piece: str) -> str:
            row_fragment = f"<tr><td>{escape(piece)}</td></tr>"
            return self._build_table_html(header_html, [row_fragment])

        return self._hard_split_wrapped(
            row_text,
            token_num,
            wrap,
            strict=strict,
        )

    def _split_code_content(
        self,
        content: str,
        token_num: int,
        language: str = "",
        *,
        strict: bool = False,
    ) -> list[str]:
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
                chunks.extend(
                    self._split_oversized_code_line(
                        line,
                        token_num,
                        fence_start,
                        fence_end,
                        strict=strict,
                    )
                )
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

        result = chunks or [content]
        if strict and any(
            num_tokens_from_string(piece) > token_num
            for piece in result
        ):
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {token_num}."
            )
        return result

    def _split_oversized_code_line(
        self,
        line: str,
        token_num: int,
        fence_start: str,
        fence_end: str,
        *,
        strict: bool = False,
    ) -> list[str]:
        return self._hard_split_wrapped(
            line,
            token_num,
            lambda piece: self._wrap_code_lines([piece], fence_start, fence_end),
            strict=strict,
        )

    def _hard_split_wrapped(
        self,
        text: str,
        token_num: int,
        wrap,
        *,
        strict: bool = False,
    ) -> list[str]:
        tokens = encoder.encode(text)
        limit = max(int(token_num), 1)
        if strict and num_tokens_from_string(wrap("")) > limit:
            raise ValueError(
                f"Structured wrapper cannot fit within token limit {limit}."
            )
        if strict and not tokens:
            return [wrap("")]
        chunks: list[str] = []
        index = 0
        while index < len(tokens):
            boundary = self._find_wrapped_token_boundary(
                tokens,
                index,
                limit,
                wrap,
                strict=strict,
            )
            if boundary is None:
                if strict:
                    raise ValueError(
                        f"Structured content cannot fit within token limit {limit}."
                    )
                raise RuntimeError(
                    f"Unable to find a valid UTF-8 boundary from token index {index}."
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
        *,
        strict: bool = False,
    ) -> tuple[int, str] | None:
        search_end = min(len(tokens), start + limit)
        for end in range(search_end, start, -1):
            piece = self._decode_token_slice(tokens, start, end)
            if piece is not None and num_tokens_from_string(wrap(piece)) <= limit:
                return end, piece

        if not strict:
            for end in range(start + 1, len(tokens) + 1):
                piece = self._decode_token_slice(tokens, start, end)
                if piece is not None:
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
