"""Collapse unit-level retrieval candidates back to chunk candidates.

Multimodal recall and rerank operate on retrieval units; this module folds
unit scores back to their source chunk (chunk score = max of its unit scores)
and trims an over-limit unit candidate list for rerank (drop lowest-scoring
units, logging a warning) without ever failing the request.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..models.chunk import DocumentChunk
from ..models.retrieval_unit import RetrievalUnitKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnitCandidate:
    """A recalled/reranked unit bound to its materialized chunk snapshot."""

    unit_id: str
    chunk_id: str
    return_chunk_id: str
    kind: RetrievalUnitKind
    asset_file_id: str | None
    content: str
    score: float
    chunk: DocumentChunk


def _finite(score: float) -> float:
    return score if math.isfinite(score) else 0.0


def collapse_units_to_chunks(
    candidates: Sequence[UnitCandidate],
) -> list[DocumentChunk]:
    """Fold unit candidates into chunk candidates ordered by max unit score."""

    best_by_chunk: dict[str, tuple[float, UnitCandidate, int]] = {}
    for order, candidate in enumerate(candidates):
        score = _finite(candidate.score)
        key = candidate.return_chunk_id
        existing = best_by_chunk.get(key)
        if existing is None or score > existing[0]:
            best_by_chunk[key] = (score, candidate, order)
    ranked = sorted(
        best_by_chunk.values(),
        key=lambda item: (-item[0], item[2]),
    )
    result: list[DocumentChunk] = []
    for score, candidate, _ in ranked:
        chunk = candidate.chunk.model_copy(deep=True)
        metadata = dict(chunk.metadata or {})
        metadata["score"] = score
        # Mark child hits so downstream parent resolution swaps in the parent.
        # A unit whose return target differs from its own chunk is a child.
        if candidate.return_chunk_id and candidate.return_chunk_id != candidate.chunk_id:
            metadata.setdefault("chunk_type", "child")
            metadata["parent_id"] = candidate.return_chunk_id
        chunk.metadata = metadata
        result.append(chunk)
    return result


def select_units_for_rerank(
    candidates: Sequence[UnitCandidate],
    *,
    max_text: int,
    max_image: int,
) -> list[UnitCandidate]:
    """Trim an over-limit unit list by dropping lowest-scoring units.

    Text units keep the top ``max_text`` by score; image units keep the top
    ``max_image`` by score. A warning is logged whenever anything is dropped.
    """

    text = [(i, c) for i, c in enumerate(candidates) if c.kind is RetrievalUnitKind.TEXT]
    image = [(i, c) for i, c in enumerate(candidates) if c.kind is RetrievalUnitKind.IMAGE]
    if len(text) <= max_text and len(image) <= max_image:
        return list(candidates)
    kept_text = sorted(text, key=lambda item: -_finite(item[1].score))[:max_text]
    kept_image = sorted(image, key=lambda item: -_finite(item[1].score))[:max_image]
    logger.warning(
        "event=kb_multimodal_rerank_units_trimmed "
        "text_before=%s text_after=%s image_before=%s image_after=%s",
        len(text),
        len(kept_text),
        len(image),
        len(kept_image),
    )
    # Unit IDs can repeat across knowledge bases; select exact input positions.
    kept_positions = {i for i, _ in (*kept_text, *kept_image)}
    return [c for i, c in enumerate(candidates) if i in kept_positions]


__all__ = ["UnitCandidate", "collapse_units_to_chunks", "select_units_for_rerank"]
