"""Retrieval unit contracts for multimodal (qwen3-vl) knowledge bases.

A multimodal chunk is decomposed into independent retrieval units:
one optional text unit (content = chunk_retrieval_content / vision_text) plus
one image unit per attached asset. Each unit is embedded and indexed
independently so that vector recall and rerank operate on units, keeping the
candidate count aligned with the requested top_n. Units collapse back to their
source chunk (by ``chunk_id``) after rerank. ``chunk_record`` units carry no
vector and exist only as the authoritative per-chunk document for listing,
parent resolution and CRUD; they are excluded from recall.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, Field
from redbear_model import ImageEmbeddingContent

from .chunk import DocumentChunk, chunk_retrieval_content

_UNIT_NAMESPACE = uuid.UUID("6f1c9a2e-7b3a-4f5d-9c2e-1a8b4d6e7f90")
MAX_IMAGES_PER_CHUNK = 10


class RetrievalUnitKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    CHUNK_RECORD = "chunk_record"


class RetrievalUnit(BaseModel):
    """One independently embedded/indexed retrieval unit of a chunk."""

    unit_id: str
    chunk_id: str
    return_chunk_id: str
    kind: RetrievalUnitKind
    unit_index: int = 0
    asset_file_id: str | None = None
    content: str = ""
    metadata: dict = Field(default_factory=dict)


def unit_id_for(doc_id: str, kind: RetrievalUnitKind, unit_index: int) -> str:
    """Stable, idempotent unit id so re-indexing does not duplicate docs."""

    return uuid.uuid5(_UNIT_NAMESPACE, f"{doc_id}:{kind.value}:{unit_index}").hex


def _chunk_id(chunk: DocumentChunk) -> str:
    metadata = chunk.metadata or {}
    doc_id = metadata.get("doc_id")
    if doc_id:
        return str(doc_id)
    document_id = metadata.get("document_id")
    sort_id = metadata.get("sort_id")
    if document_id is not None and sort_id is not None:
        return f"{document_id}:{sort_id}"
    return uuid.uuid5(_UNIT_NAMESPACE, f"content:{chunk.page_content}").hex


def _return_chunk_id(chunk: DocumentChunk, chunk_id: str) -> str:
    metadata = chunk.metadata or {}
    if metadata.get("chunk_type") == "child" and metadata.get("parent_id"):
        return str(metadata["parent_id"])
    return chunk_id


def _asset_file_ids(chunk: DocumentChunk) -> list[str]:
    metadata = chunk.metadata or {}
    raw_ids = metadata.get("asset_file_ids")
    if not isinstance(raw_ids, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in raw_ids:
        normalized = str(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) == MAX_IMAGES_PER_CHUNK:
            break
    return result


def build_retrieval_units(
    chunk: DocumentChunk,
    images: Mapping[str, ImageEmbeddingContent] | None = None,
) -> list[RetrievalUnit]:
    """Decompose one chunk into its retrieval units.

    ``images`` maps resolved asset_file_id -> ImageEmbeddingContent and is only
    used to confirm an image asset is resolvable; image bytes never enter the
    unit (only ``asset_file_id`` is recorded).
    """

    metadata = dict(chunk.metadata or {})
    chunk_type = metadata.get("chunk_type")
    chunk_id = _chunk_id(chunk)
    return_chunk_id = _return_chunk_id(chunk, chunk_id)

    record = RetrievalUnit(
        unit_id=unit_id_for(chunk_id, RetrievalUnitKind.CHUNK_RECORD, 0),
        chunk_id=chunk_id,
        return_chunk_id=return_chunk_id,
        kind=RetrievalUnitKind.CHUNK_RECORD,
        content=chunk.page_content,
        metadata=metadata,
    )
    if chunk_type in {"source", "parent"}:
        return [record]

    units = [record]
    if chunk_type == "qa":
        text = chunk_retrieval_content(chunk)
        if text.strip():
            units.append(
                RetrievalUnit(
                    unit_id=unit_id_for(chunk_id, RetrievalUnitKind.TEXT, 0),
                    chunk_id=chunk_id,
                    return_chunk_id=return_chunk_id,
                    kind=RetrievalUnitKind.TEXT,
                    content=text,
                    metadata=metadata,
                )
            )
        return units

    asset_ids = _asset_file_ids(chunk)
    if not asset_ids:
        text = chunk_retrieval_content(chunk)
        if text.strip():
            units.append(
                RetrievalUnit(
                    unit_id=unit_id_for(chunk_id, RetrievalUnitKind.TEXT, 0),
                    chunk_id=chunk_id,
                    return_chunk_id=return_chunk_id,
                    kind=RetrievalUnitKind.TEXT,
                    content=text,
                    metadata=metadata,
                )
            )
        return units

    # Image chunk: text unit uses vision_text (the image's textual form).
    vision_text = metadata.get("vision_text")
    if isinstance(vision_text, str) and vision_text.strip():
        units.append(
            RetrievalUnit(
                unit_id=unit_id_for(chunk_id, RetrievalUnitKind.TEXT, 0),
                chunk_id=chunk_id,
                return_chunk_id=return_chunk_id,
                kind=RetrievalUnitKind.TEXT,
                content=vision_text.strip(),
                metadata=metadata,
            )
        )
    for index, asset_id in enumerate(asset_ids):
        if images is not None and asset_id not in images:
            continue
        units.append(
            RetrievalUnit(
                unit_id=unit_id_for(chunk_id, RetrievalUnitKind.IMAGE, index),
                chunk_id=chunk_id,
                return_chunk_id=return_chunk_id,
                kind=RetrievalUnitKind.IMAGE,
                unit_index=index,
                asset_file_id=asset_id,
                content="",
                metadata=metadata,
            )
        )
    return units


__all__ = [
    "MAX_IMAGES_PER_CHUNK",
    "RetrievalUnit",
    "RetrievalUnitKind",
    "build_retrieval_units",
    "unit_id_for",
]
