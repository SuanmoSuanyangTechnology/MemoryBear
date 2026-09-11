"""Unified rerank strategy execution for retrieval candidates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from redbear_model import ImageEmbeddingContent

from ...api.schemas.rerank import RerankMode
from ..models.chunk import DocumentChunk
from .candidates import (
    aggregate_chunk_channel_scores,
    candidate_identity,
    has_unit_candidates,
    materialize_candidates,
    retrieval_identity,
    select_top_chunk_units,
)
from .models import ModelRuntimeSnapshot, RerankPlan, RetrievalCandidate


@dataclass(frozen=True)
class ModelRerankResult:
    chunks: tuple[DocumentChunk, ...]
    used_fallback: bool


ModelRanker = Callable[
    [ModelRuntimeSnapshot, str | ImageEmbeddingContent, Sequence[DocumentChunk], int],
    Awaitable[ModelRerankResult],
]


def _fallback_score(candidate: RetrievalCandidate) -> float:
    if candidate.final_score is not None:
        return candidate.final_score
    metadata_score = (candidate.chunk.metadata or {}).get("score")
    return float(metadata_score) if metadata_score is not None else 0.5


class _WeightedScoreAdapter:
    async def rank(
        self,
        *,
        query: str | ImageEmbeddingContent,
        candidates: Sequence[RetrievalCandidate],
        plan: RerankPlan,
    ) -> list[RetrievalCandidate]:
        del query
        if has_unit_candidates(candidates):
            candidates = aggregate_chunk_channel_scores(candidates)
        ranked = [
            candidate.with_final_score(
                plan.weights.semantic_weight * (candidate.semantic_score or 0.0)
                + plan.weights.participle_weight * (candidate.participle_score or 0.0)
            )
            for candidate in candidates
        ]
        return sorted(
            ranked,
            key=lambda candidate: (-candidate.final_score, candidate.arrival_index),
        )


class _ModelRerankAdapter:
    def __init__(self, model_ranker: ModelRanker | None) -> None:
        self._model_ranker = model_ranker

    async def rank(
        self,
        *,
        query: str | ImageEmbeddingContent,
        candidates: Sequence[RetrievalCandidate],
        plan: RerankPlan,
        top_k: int,
    ) -> list[RetrievalCandidate]:
        if plan.model is None or self._model_ranker is None:
            return self._fallback(candidates[:top_k])

        canonical_chunks = materialize_candidates(candidates)
        model_result = await self._model_ranker(
            plan.model,
            query,
            canonical_chunks,
            top_k,
        )
        if model_result.used_fallback:
            return self._fallback(candidates[:top_k])

        candidates_by_identity = {
            candidate_identity(candidate): candidate for candidate in candidates
        }
        result: list[RetrievalCandidate] = []
        for chunk in model_result.chunks:
            identity = retrieval_identity(chunk)
            candidate = candidates_by_identity.get(identity)
            if candidate is None:
                continue
            public_score = (chunk.metadata or {}).get("score")
            result.append(candidate.with_final_score(float(public_score or 0.0)))
        return result

    @staticmethod
    def _fallback(
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        return [
            candidate.with_final_score(_fallback_score(candidate))
            for candidate in candidates
        ]


class RerankEngine:
    def __init__(self, model_ranker: ModelRanker | None) -> None:
        self._model = _ModelRerankAdapter(model_ranker)
        self._weighted = _WeightedScoreAdapter()

    async def rank(
        self,
        *,
        query: str | ImageEmbeddingContent,
        candidates: Sequence[RetrievalCandidate],
        plan: RerankPlan,
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievalCandidate]:
        unit_ranking = has_unit_candidates(candidates)
        if plan.mode is RerankMode.WEIGHTED_SCORE:
            ranked = await self._weighted.rank(
                query=query,
                candidates=candidates,
                plan=plan,
            )
        else:
            ranked = await self._model.rank(
                query=query,
                candidates=candidates,
                plan=plan,
                top_k=len(candidates) if unit_ranking else top_k,
            )
        filtered = [
            candidate
            for candidate in ranked
            if candidate.final_score is not None
            and candidate.final_score > score_threshold
        ]
        if unit_ranking:
            return select_top_chunk_units(filtered, top_k)
        return filtered[:top_k]


__all__ = ["ModelRanker", "ModelRerankResult", "RerankEngine"]
