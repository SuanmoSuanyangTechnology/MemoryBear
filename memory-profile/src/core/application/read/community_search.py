"""Community / scene retrieval.

Ported from the host's ``read_services/search_engine/community_search.py``. The
policy (top-k, cosine floors, token budget) is server-owned; the request cannot
override it. Only the storage recall is delegated to a port, so this module stays
pure application logic (numpy for vector validation, tiktoken for budgeting).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from numbers import Real
from typing import Any

import numpy as np
import tiktoken

from src.core.application.read.projection import emit_community_stage
from src.core.domain.enums import MemorySource
from src.core.domain.errors import CommunityRetrievalError
from src.core.domain.models import (
    CommunityEvidence,
    CommunityRetrievalTrace,
    MemoryItem,
    ReadResult,
    RetrievalExecutionTrace,
)
from src.core.domain.ports import (
    CandidateBatch,
    CommunityRecallQuery,
    EmbeddingClient,
)


@dataclass(frozen=True)
class CommunityDefaults:
    """Server-owned community retrieval policy (no request overrides)."""

    community_top_k: int = 7
    member_top_k: int = 15
    evidence_top_k: int = 15
    community_min_cosine: float = 0.2
    scene_min_cosine: float = 0.2
    context_token_budget: int = 2400
    budget_tokenizer: str = "cl100k_base"
    retrieval_timeout_seconds: int = 60

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


COMMUNITY_DEFAULTS = CommunityDefaults()


def _validate_query_vector(value: Any) -> list[float]:
    """Validate the query embedding without changing its magnitude or direction."""

    try:
        if not isinstance(value, (list, tuple, np.ndarray)):
            raise ValueError
        if any(
            not isinstance(item, Real) or isinstance(item, (bool, np.bool_))
            for item in value
        ):
            raise ValueError
        vector = np.asarray(value, dtype=np.float64)
        if (
            vector.ndim != 1
            or vector.size == 0
            or not np.isfinite(vector).all()
            or not vector.any()
        ):
            raise ValueError
        return vector.tolist()
    except (TypeError, ValueError, OverflowError) as exc:
        raise CommunityRetrievalError("invalid_vector") from exc


def _evidence_tokens(records: list[CommunityEvidence]) -> int:
    """Sum each node's text tokens, excluding JSON syntax and metadata."""

    tokenizer = tiktoken.get_encoding(COMMUNITY_DEFAULTS.budget_tokenizer)
    return sum(
        len(tokenizer.encode(record.text, disallowed_special=())) for record in records
    )


def _pack_evidence(
    ranked: list[tuple[float, dict[str, Any]]],
) -> tuple[list[CommunityEvidence], int, list[dict[str, Any]]]:
    """Deduplicate bodies ignoring whitespace, then fit whole nodes below budget."""

    policy = COMMUNITY_DEFAULTS
    records: list[CommunityEvidence] = []
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, scene in ranked:
        if len(records) >= policy.evidence_top_k:
            break
        text = scene["content"]
        key = "".join(text.split())
        if not key or key in seen:
            actions.append(
                {"node_id": scene["id"], "action": "duplicate" if key else "empty_body"}
            )
            continue
        seen.add(key)
        records.append(CommunityEvidence(node_id=scene["id"], text=text))
    token_counts = [_evidence_tokens([record]) for record in records]
    tokens = sum(token_counts)
    while records and tokens >= policy.context_token_budget:
        removed = records.pop()
        tokens -= token_counts.pop()
        actions.append({"node_id": removed.node_id, "action": "budget_skipped"})
    return records, tokens, actions


class CommunitySearchService:
    """Community recall, scene recall and evidence packing for one query."""

    def __init__(
        self,
        *,
        recall_candidates: Callable[..., Awaitable[CandidateBatch]],
        end_user_id: str,
        embedder: EmbeddingClient | None,
        model_config_id: str,
        model_name: str,
        operation_id: str,
        semantic_degraded: bool = False,
    ) -> None:
        if not end_user_id:
            raise CommunityRetrievalError("invalid_ownership")
        self.recall_candidates = recall_candidates
        self.end_user_id = end_user_id
        self.embedder = embedder
        self.operation_id = operation_id
        self.semantic_degraded = semantic_degraded
        self.trace = CommunityRetrievalTrace(
            parameters=COMMUNITY_DEFAULTS.as_dict(),
            model_config_id=model_config_id,
            model_name=model_name,
            semantic_degraded=semantic_degraded,
        )

    async def search(
        self, query: str, *, compatibility_parameters: dict[str, Any]
    ) -> ReadResult:
        self.trace.ignored_compatibility_parameters = compatibility_parameters
        try:
            with timed_stage(self.trace, "total"):
                async with asyncio.timeout(
                    COMMUNITY_DEFAULTS.retrieval_timeout_seconds
                ):
                    result = await self._search(query)
            self.trace.stage_duration_ms["total"] += self.trace.stage_duration_ms.get(
                "initialization", 0
            )
            self.trace.status = "completed"
            return result
        except asyncio.CancelledError:
            self.trace.status, self.trace.error_reason = "failed", "cancelled"
            raise
        except Exception as exc:
            self.trace.status = "failed"
            self.trace.error_reason = (
                str(exc)
                if isinstance(exc, CommunityRetrievalError)
                else "scan_timeout"
                if isinstance(exc, TimeoutError)
                else "dependency_failed"
            )
            raise

    def _validate_node(
        self,
        node: dict[str, Any],
        *,
        scene: bool,
        community_id: str | None = None,
    ) -> None:
        if not isinstance(node, Mapping) or not isinstance(node.get("id"), str) or not node["id"]:
            raise CommunityRetrievalError("invalid_node_id")
        if node.get("end_user_id") != self.end_user_id:
            raise CommunityRetrievalError("invalid_ownership")
        if node.get("delete_at") is not None:
            raise CommunityRetrievalError("data_changed")
        if not isinstance(node.get("content" if scene else "summary"), str):
            raise CommunityRetrievalError("invalid_node_data")
        if scene and (
            node.get("community_status") != "ACTIVE"
            or node.get("community_eligibility") != "ELIGIBLE"
            or node.get("scene_community_id") != community_id
        ):
            raise CommunityRetrievalError("invalid_ownership")

    async def _recall(
        self, query: str, *, community_id: str | None = None
    ) -> tuple[list[tuple[float, dict[str, Any]]], int]:
        fulltext = self.semantic_degraded
        scene = community_id is not None
        label = MemorySource.SCENE_SUMMARY if scene else MemorySource.SCENE_COMMUNITY
        policy = COMMUNITY_DEFAULTS
        limit = policy.member_top_k if scene else policy.community_top_k
        threshold = policy.scene_min_cosine if scene else policy.community_min_cosine
        recall_query = CommunityRecallQuery(
            end_user_id=self.end_user_id,
            community_id=community_id,
        )
        value: list[float] | str = query
        if not fulltext:
            previous_embedding_ms = self.trace.stage_duration_ms.get("embedding", 0)
            try:
                with timed_stage(self.trace, "embedding"):
                    self.trace.embedding_calls += 1
                    if self.embedder is None:
                        raise CommunityRetrievalError("missing_model_configuration")
                    value = _validate_query_vector(
                        await self.embedder.aembed_query(query)
                    )
                    self.trace.vector_dimension = len(value)
            finally:
                self.trace.stage_duration_ms["embedding"] += previous_embedding_ms
        self.trace.database_requests += 1
        result = await self.recall_candidates(
            recall_query,
            value,
            limit=limit,
        )
        self.trace.backend = result.backend
        candidates: list[tuple[float, dict[str, Any]]] = []
        for item in result.items:
            node, score = item.data, item.data.get("score")
            self._validate_node(node, scene=scene, community_id=community_id)
            if item.label != label.value:
                raise CommunityRetrievalError("invalid_node_id")
            topic_scope = node.get("topic_scope")
            if not isinstance(topic_scope, str):
                continue
            topic_scope = topic_scope.strip()
            if not topic_scope:
                continue
            node["topic_scope"] = topic_scope
            if not fulltext and score < threshold - 1e-6:
                continue
            candidates.append((float(score), node))
        return (
            sorted(candidates, key=lambda pair: (-pair[0], pair[1]["id"])),
            len(result.items),
        )

    def _result(
        self,
        *,
        memories: list[MemoryItem] | None = None,
        content: str = "",
        reason: str | None = None,
    ) -> ReadResult:
        self.trace.empty_reason = reason  # type: ignore[assignment]
        if not memories:
            self.trace.final_tokens = 0
        return ReadResult(
            memories=memories or [],
            content_str=content,
            community_trace=self.trace,
            execution_trace=RetrievalExecutionTrace(
                keyword_status="completed" if self.semantic_degraded else "skipped",
                semantic_status="degraded" if self.semantic_degraded else "completed",
                keyword_hit_count=len(self.trace.merged_ranking)
                if self.semantic_degraded
                else 0,
                semantic_hit_count=0
                if self.semantic_degraded
                else len(self.trace.merged_ranking),
                degraded_reasons=["model_not_ready"] if self.semantic_degraded else [],
                raw_hit_count=sum(len(item["candidates"]) for item in self.trace.members),
                merged_count=len(memories or []),
                limit=COMMUNITY_DEFAULTS.evidence_top_k,
            ),
        )

    async def _search(self, query: str) -> ReadResult:
        emit_community_stage(self.trace, "community_searched", "running")
        with timed_stage(self.trace, "community_selection"):
            selected, recalled_community_count = await self._recall(query)
            self.trace.recalled_community_count = recalled_community_count
        self.trace.selected_communities = [
            {
                "id": node["id"],
                "score": score,
                "topic_scope": node["topic_scope"],
                "name": node.get("topic_name"),
            }
            for score, node in selected
        ]
        emit_community_stage(self.trace, "community_searched", "completed")
        if not selected:
            emit_community_stage(self.trace, "community_scenes_searched", "skipped")
            emit_community_stage(self.trace, "community_evidence_packed", "skipped")
            return self._result(reason="no_community_hit")
        merged: dict[str, tuple[float, dict[str, Any]]] = {}
        emit_community_stage(self.trace, "community_scenes_searched", "running")
        with timed_stage(self.trace, "scene_selection"):
            for _, community in selected:
                candidates, recalled_scene_count = await self._recall(
                    query, community_id=community["id"]
                )
                self.trace.members.append(
                    {
                        "community_id": community["id"],
                        "matched_scene_count": None,
                        "recalled_scene_count": recalled_scene_count,
                        "candidates": [
                            {
                                "id": node["id"],
                                "score": score,
                                "topic_scope": node["topic_scope"],
                                "content": node["content"],
                            }
                            for score, node in candidates
                        ],
                    }
                )
                emit_community_stage(self.trace, "community_scenes_searched", "running")
                for score, node in candidates:
                    if node["id"] in merged:
                        self.trace.packing_actions.append(
                            {
                                "node_id": node["id"],
                                "action": "id_merged",
                                "merged_into": node["id"],
                            }
                        )
                    merged[node["id"]] = (score, node)
        ranked = sorted(merged.values(), key=lambda pair: (-pair[0], pair[1]["id"]))
        self.trace.merged_ranking = [
            {"id": node["id"], "score": score} for score, node in ranked
        ]
        emit_community_stage(self.trace, "community_scenes_searched", "completed")
        if not ranked:
            emit_community_stage(self.trace, "community_evidence_packed", "skipped")
            return self._result(reason="no_scene_hit_in_communities")
        emit_community_stage(self.trace, "community_evidence_packed", "running")
        with timed_stage(self.trace, "evidence_packing"):
            records, tokens, actions = _pack_evidence(ranked)
            self.trace.final_tokens = tokens
            self.trace.packing_actions.extend(actions)
            self.trace.evidence_ids = [record.node_id for record in records]
        emit_community_stage(self.trace, "community_evidence_packed", "completed")
        if not records:
            return self._result(reason="no_valid_evidence")
        memories = [
            MemoryItem(
                id=record.node_id,
                source=MemorySource.SCENE_SUMMARY,
                score=merged[record.node_id][0],
                content=record.text,
                query=query,
            )
            for record in records
        ]
        return self._result(
            memories=memories, content="\n".join(record.text for record in records)
        )


class timed_stage:
    """Record one community stage duration in milliseconds."""

    def __init__(self, trace: CommunityRetrievalTrace, stage: str) -> None:
        self._trace = trace
        self._stage = stage
        self._started = 0.0

    def __enter__(self) -> CommunityRetrievalTrace:
        import time

        self._started = time.perf_counter()
        return self._trace

    def __exit__(self, *exc_info: object) -> None:
        import time

        self._trace.stage_duration_ms[self._stage] = round(
            (time.perf_counter() - self._started) * 1000, 3
        )


__all__ = [
    "COMMUNITY_DEFAULTS",
    "CommunityDefaults",
    "CommunitySearchService",
    "timed_stage",
]