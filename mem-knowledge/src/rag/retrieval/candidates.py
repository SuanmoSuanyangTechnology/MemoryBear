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
    return retrieval_identity(candidate.chunk, candidate.knowledge_id)


def retrieval_identity(
    chunk: DocumentChunk, knowledge_id: uuid.UUID | None = None,
) -> tuple[Any, ...]:
    """Keep unit identity until ranking; legacy candidates retain chunk identity."""

    metadata = chunk.metadata or {}
    if metadata.get("_unit_id"):
        return (
            "unit_id",
            str(knowledge_id or metadata.get("knowledge_id") or ""),
            metadata["_unit_id"],
        )
    return chunk_identity(chunk)


def has_unit_candidates(candidates: Sequence[RetrievalCandidate]) -> bool:
    return any((candidate.chunk.metadata or {}).get("_unit_id") for candidate in candidates)


def _chunk_key(candidate: RetrievalCandidate) -> tuple[Any, ...]:
    return (candidate.knowledge_id, *chunk_identity(candidate.chunk))


def _return_chunk_key(candidate: RetrievalCandidate) -> tuple[Any, ...]:
    metadata = candidate.chunk.metadata or {}
    return_id = metadata.get("_return_chunk_id")
    if metadata.get("_unit_id") and return_id:
        return (candidate.knowledge_id, "doc_id", str(return_id))
    return _chunk_key(candidate)


def _rank_score(candidate: RetrievalCandidate) -> float:
    return candidate.final_score if candidate.final_score is not None else _score(candidate.chunk)


def collapse_ranked_candidates(
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    """Choose the highest-scoring unit of each return chunk after ranking."""

    best: dict[tuple[Any, ...], RetrievalCandidate] = {}
    for candidate in candidates:
        key = _return_chunk_key(candidate)
        if key not in best or _rank_score(candidate) > _rank_score(best[key]):
            best[key] = candidate
    return sorted(best.values(), key=_rank_score, reverse=True)


def select_top_chunk_units(
    candidates: Sequence[RetrievalCandidate], top_k: int,
) -> list[RetrievalCandidate]:
    """Limit distinct chunks while keeping their units for subsequent model stages."""

    selected = {
        _return_chunk_key(candidate) for candidate in collapse_ranked_candidates(candidates)[:top_k]
    }
    return [candidate for candidate in candidates if _return_chunk_key(candidate) in selected]


def aggregate_chunk_channel_scores(
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    """Preserve pre-unitization weighted scoring without discarding unit identities."""

    totals: dict[tuple[Any, ...], RetrievalCandidate] = {}
    for candidate in candidates:
        key = _chunk_key(candidate)
        previous = totals.get(key, candidate)
        totals[key] = replace(
            previous,
            semantic_score=_maximum(previous.semantic_score, candidate.semantic_score),
            participle_score=_maximum(previous.participle_score, candidate.participle_score),
            graph_score=_maximum(previous.graph_score, candidate.graph_score),
        )
    return [
        replace(
            candidate,
            semantic_score=totals[_chunk_key(candidate)].semantic_score,
            participle_score=totals[_chunk_key(candidate)].participle_score,
            graph_score=totals[_chunk_key(candidate)].graph_score,
        )
        for candidate in candidates
    ]


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
        if chunk.metadata.get("_unit_id"):
            chunk.metadata["knowledge_id"] = str(candidate.knowledge_id)
        result.append(chunk)
    return result


__all__ = [
    "aggregate_chunk_channel_scores",
    "RetrievalChannel",
    "candidate_identity",
    "candidate_from_chunk",
    "chunk_identity",
    "collapse_ranked_candidates",
    "deduplicate_candidates_first_win",
    "materialize_candidates",
    "merge_candidates",
    "has_unit_candidates",
    "retrieval_identity",
    "select_top_chunk_units",
]
