from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from app.core.rag.common.token_utils import num_tokens_from_string
from app.core.rag.knowledge_graph.models import ExtractionBatch, SourceChunk


_SOURCE_CHUNK_TYPES = frozenset({"chunk", "source", "child"})


def _to_sort_id(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def select_source_chunks(raw_hits: Sequence[Mapping[str, Any]]) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    for hit in raw_hits:
        source = hit.get("_source")
        if not isinstance(source, Mapping):
            continue
        metadata = source.get("metadata")
        if not isinstance(metadata, Mapping):
            continue

        page_content = source.get("page_content")
        if not isinstance(page_content, str) or not page_content.strip():
            continue

        source_chunk_id = metadata.get("doc_id")
        document_id = metadata.get("document_id")
        if source_chunk_id is None or document_id is None:
            continue

        chunk_type = str(metadata.get("chunk_type") or "chunk").strip().lower()
        if chunk_type not in _SOURCE_CHUNK_TYPES:
            continue

        parent_id = metadata.get("parent_id")
        chunks.append(
            SourceChunk(
                source_chunk_id=str(source_chunk_id),
                document_id=str(document_id),
                page_content=page_content,
                sort_id=_to_sort_id(metadata.get("sort_id")),
                chunk_type=chunk_type,
                parent_id=str(parent_id) if parent_id is not None else None,
            )
        )

    return sorted(
        chunks,
        key=lambda chunk: (chunk.sort_id, chunk.source_chunk_id),
    )


def _render_chunk(chunk: SourceChunk) -> str:
    source_chunk_id = escape(chunk.source_chunk_id, quote=True)
    return (
        f'<source_chunk id="{source_chunk_id}">\n'
        f"{chunk.page_content}\n"
        "</source_chunk>"
    )


def build_extraction_batches(
    chunks: Sequence[SourceChunk],
    max_tokens: int,
) -> list[ExtractionBatch]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")

    batches: list[ExtractionBatch] = []
    current_texts: list[str] = []
    current_ids: list[str] = []

    def flush() -> None:
        if not current_texts:
            return
        batches.append(
            ExtractionBatch(
                text="\n\n".join(current_texts),
                source_chunk_ids=tuple(current_ids),
            )
        )
        current_texts.clear()
        current_ids.clear()

    for chunk in chunks:
        rendered = _render_chunk(chunk)
        candidate = "\n\n".join((*current_texts, rendered))
        if current_texts and num_tokens_from_string(candidate) > max_tokens:
            flush()

        current_texts.append(rendered)
        current_ids.append(chunk.source_chunk_id)

        if num_tokens_from_string(rendered) > max_tokens:
            flush()

    flush()
    return batches
