"""Request-local multimodal Chunk preparation contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from redbear_model import EmbeddingContent, ImageEmbeddingContent, TextEmbeddingContent

from .chunk import DocumentChunk

_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")


@dataclass(frozen=True)
class PreparedChunk:
    chunk: DocumentChunk
    embedding_contents: tuple[EmbeddingContent, ...]


def collect_asset_file_ids(chunks: Sequence[DocumentChunk]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        metadata = chunk.metadata or {}
        if metadata.get("chunk_type") == "qa":
            continue
        raw_ids = metadata.get("asset_file_ids")
        if not isinstance(raw_ids, list):
            continue
        for value in raw_ids:
            normalized = str(value)
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
    return result


def sanitized_retrieval_text(chunk: DocumentChunk) -> str:
    from .chunk import chunk_retrieval_content

    return _MARKDOWN_IMAGE_PATTERN.sub("", chunk_retrieval_content(chunk)).strip()


def prepare_chunk_embedding_contents(
    chunk: DocumentChunk,
    images: Mapping[str, ImageEmbeddingContent],
) -> tuple[EmbeddingContent, ...]:
    metadata = chunk.metadata or {}
    if metadata.get("chunk_type") in {"source", "parent"}:
        return ()
    text = sanitized_retrieval_text(chunk)
    text_content = TextEmbeddingContent(text=text) if text else None
    if metadata.get("chunk_type") == "qa":
        return (text_content,) if text_content is not None else ()
    image_contents: list[EmbeddingContent] = []
    raw_ids = metadata.get("asset_file_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            image = images.get(str(value))
            if image is None:
                continue
            image_contents.append(image)
            if len(image_contents) == 10:
                break
    text_contents: list[EmbeddingContent] = (
        [text_content] if text_content is not None else []
    )
    page_text = _MARKDOWN_IMAGE_PATTERN.sub("", chunk.page_content).strip()
    if image_contents and not page_text:
        return tuple([*image_contents, *text_contents])
    return tuple([*text_contents, *image_contents])


__all__ = [
    "PreparedChunk",
    "collect_asset_file_ids",
    "prepare_chunk_embedding_contents",
    "sanitized_retrieval_text",
]
