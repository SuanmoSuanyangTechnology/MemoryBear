"""Select source chunks and create single-source extraction batches."""

from collections.abc import Mapping, Sequence
from typing import Any

from .models import ExtractionBatch, SourceChunk

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
    return sorted(chunks, key=lambda chunk: (chunk.sort_id, chunk.source_chunk_id))


def build_extraction_batches(chunks: Sequence[SourceChunk]) -> list[ExtractionBatch]:
    return [
        ExtractionBatch(text=chunk.page_content, source_chunk_ids=(chunk.source_chunk_id,))
        for chunk in chunks
    ]


__all__ = ["build_extraction_batches", "select_source_chunks"]
