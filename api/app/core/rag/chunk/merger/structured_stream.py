from dataclasses import dataclass, field

from app.core.rag.chunk.context import ParsedBlock, ParsedBlockType

PROTECTED_DELIMITER_TYPES = {
    ParsedBlockType.IMAGE,
    ParsedBlockType.CODE,
    ParsedBlockType.TABLE,
}


@dataclass(frozen=True)
class BlockSpan:
    source_key: int
    block: ParsedBlock
    start: int
    end: int


@dataclass(frozen=True)
class SourceFragment:
    source_key: int
    block: ParsedBlock
    content: str
    complete: bool
    structure_valid: bool = True


@dataclass
class StructuredStream:
    text: str
    spans: list[BlockSpan] = field(default_factory=list)


def block_separator(previous: ParsedBlock, current: ParsedBlock) -> str:
    return "\n\n" if current.type is ParsedBlockType.HEADING else "\n"


def rebuild_structured_stream(blocks: list[ParsedBlock]) -> StructuredStream:
    text_parts: list[str] = []
    spans: list[BlockSpan] = []
    previous: ParsedBlock | None = None
    offset = 0

    for source_key, block in enumerate(blocks):
        content = str(block.content or "")
        if not content:
            continue

        if previous is not None:
            separator = block_separator(previous, block)
            text_parts.append(separator)
            offset += len(separator)

        start = offset
        text_parts.append(content)
        offset += len(content)
        spans.append(BlockSpan(source_key=source_key, block=block, start=start, end=offset))
        previous = block

    return StructuredStream(text="".join(text_parts), spans=spans)


def split_structured_stream(stream: StructuredStream, delimiter: str | None) -> list[StructuredStream]:
    if not delimiter:
        return [stream]

    ranges: list[tuple[int, int]] = []
    segment_start = 0
    search_start = 0
    delimiter_length = len(delimiter)

    while True:
        delimiter_start = stream.text.find(delimiter, search_start)
        if delimiter_start < 0:
            break

        delimiter_end = delimiter_start + delimiter_length
        search_start = delimiter_end
        if _intersects_protected_span(stream.spans, delimiter_start, delimiter_end):
            continue

        segment_end = delimiter_end if delimiter in {"。", "；"} else delimiter_start
        if segment_start != segment_end:
            ranges.append((segment_start, segment_end))
        segment_start = delimiter_end

    if segment_start != len(stream.text):
        ranges.append((segment_start, len(stream.text)))

    return [_stream_for_range(stream, start, end) for start, end in ranges]


def fragments_for_stream(stream: StructuredStream) -> list[SourceFragment]:
    return [
        SourceFragment(
            source_key=span.source_key,
            block=span.block,
            content=stream.text[span.start : span.end],
            complete=stream.text[span.start : span.end] == str(span.block.content or ""),
        )
        for span in stream.spans
    ]


def _intersects_protected_span(spans: list[BlockSpan], start: int, end: int) -> bool:
    return any(
        span.block.type in PROTECTED_DELIMITER_TYPES and span.start < end and start < span.end
        for span in spans
    )


def _stream_for_range(stream: StructuredStream, start: int, end: int) -> StructuredStream:
    spans = [
        BlockSpan(
            source_key=span.source_key,
            block=span.block,
            start=max(span.start, start) - start,
            end=min(span.end, end) - start,
        )
        for span in stream.spans
        if span.start < end and start < span.end
    ]
    return StructuredStream(text=stream.text[start:end], spans=spans)
