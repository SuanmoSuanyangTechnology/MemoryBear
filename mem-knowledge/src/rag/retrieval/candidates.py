"""Retrieval candidate construction and score-preserving merge helpers."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any

from ..models.chunk import DocumentChunk
from .models import RetrievalCandidate


class RetrievalChannel(StrEnum):
    SEMANTIC = "semantic"
    PARTICIPLE = "participle"
    GRAPH = "graph"


def _score(chunk: DocumentChunk) -> float:
    value = float((chunk.metadata or {}).get("score") or 0)
    return value if math.isfinite(value) else 0.0


def candidate_from_chunk(
    chunk: DocumentChunk,
    knowledge_id: uuid.UUID,
    channel: RetrievalChannel,
    arrival_index: int,
) -> RetrievalCandidate:
    score = _score(chunk)
    return RetrievalCandidate(
        chunk=chunk,
        knowledge_id=knowledge_id,
        semantic_score=score if channel is RetrievalChannel.SEMANTIC else None,
        participle_score=score if channel is RetrievalChannel.PARTICIPLE else None,
        graph_score=score if channel is RetrievalChannel.GRAPH else None,
        final_score=None,
        arrival_index=arrival_index,
    )


def chunk_identity(chunk: DocumentChunk) -> tuple[Any, ...]:
    metadata = chunk.metadata or {}
    if metadata.get("doc_id"):
        return ("doc_id", metadata["doc_id"])
    if metadata.get("document_id") is not None and metadata.get("sort_id") is not None:
        return ("document_sort", metadata["document_id"], metadata["sort_id"])
    return ("content", chunk.page_content)


def candidate_identity(candidate: RetrievalCandidate) -> tuple[Any, ...]:
    return chunk_identity(candidate.chunk)


def _maximum(left: float | None, right: float | None) -> float | None:
    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def merge_candidates(
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    merged: dict[tuple[Any, ...], RetrievalCandidate] = {}
    for candidate in candidates:
        key = candidate_identity(candidate)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        merged[key] = replace(
            existing,
            semantic_score=_maximum(existing.semantic_score, candidate.semantic_score),
            participle_score=_maximum(existing.participle_score, candidate.participle_score),
            graph_score=_maximum(existing.graph_score, candidate.graph_score),
            final_score=_maximum(existing.final_score, candidate.final_score),
        )
    return list(merged.values())


def deduplicate_candidates_first_win(
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    seen: set[tuple[Any, ...]] = set()
    result: list[RetrievalCandidate] = []
    for candidate in candidates:
        key = candidate_identity(candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def materialize_candidates(
    candidates: Sequence[RetrievalCandidate],
) -> list[DocumentChunk]:
    result: list[DocumentChunk] = []
    for candidate in candidates:
        chunk = candidate.chunk.model_copy(deep=True)
        score = candidate.final_score
        if score is None:
            score = _score(candidate.chunk)
        chunk.metadata["score"] = score
        result.append(chunk)
    return result


__all__ = [
    "RetrievalChannel",
    "candidate_identity",
    "candidate_from_chunk",
    "chunk_identity",
    "deduplicate_candidates_first_win",
    "materialize_candidates",
    "merge_candidates",
]
