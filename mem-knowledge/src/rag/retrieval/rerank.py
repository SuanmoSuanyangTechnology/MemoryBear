"""Unified rerank strategy execution for retrieval candidates."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from ...api.schemas.rerank import RerankMode
from ..models.chunk import DocumentChunk
from .candidates import candidate_identity, chunk_identity, materialize_candidates
from .models import ModelRuntimeSnapshot, RerankPlan, RetrievalCandidate


@dataclass(frozen=True)
class ModelRerankResult:
    chunks: tuple[DocumentChunk, ...]
    used_fallback: bool


ModelRanker = Callable[
    [ModelRuntimeSnapshot, str, Sequence[DocumentChunk], int],
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
        query: str,
        candidates: Sequence[RetrievalCandidate],
        plan: RerankPlan,
    ) -> list[RetrievalCandidate]:
        del query
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
        query: str,
        candidates: Sequence[RetrievalCandidate],
        plan: RerankPlan,
    ) -> list[RetrievalCandidate]:
        if plan.model is None or self._model_ranker is None:
            return self._fallback(candidates)

        canonical_chunks = materialize_candidates(candidates)
        try:
            model_result = await self._model_ranker(
                plan.model,
                query,
                canonical_chunks,
                len(canonical_chunks),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if not plan.compatibility_fallback:
                raise
            return self._fallback(candidates)

        candidates_by_identity = {
            candidate_identity(candidate): candidate for candidate in candidates
        }
        result: list[RetrievalCandidate] = []
        for chunk in model_result.chunks:
            identity = chunk_identity(chunk)
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
        query: str,
        candidates: Sequence[RetrievalCandidate],
        plan: RerankPlan,
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievalCandidate]:
        adapter = self._weighted if plan.mode is RerankMode.WEIGHTED_SCORE else self._model
        ranked = await adapter.rank(query=query, candidates=candidates, plan=plan)
        filtered = [
            candidate
            for candidate in ranked
            if candidate.final_score is not None
            and candidate.final_score > score_threshold
        ]
        return filtered[:top_k]


__all__ = ["ModelRanker", "ModelRerankResult", "RerankEngine"]
