"""Community and Scene retrieval, using full text while embeddings are rebuilding."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from numbers import Real

import numpy as np
import tiktoken

from app.core.memory.exceptions import CommunityRetrievalError
from app.core.memory.models.community_retrieval_models import CommunityEvidence, CommunityRetrievalTrace
from app.core.memory.models.service_models import Memory, MemorySearchResult
from app.core.memory.retrieval_trace.community_trace import log_community_trace, timed_stage
from app.core.memory.retrieval_trace.models import RetrievalExecutionTrace
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.models import FilterCondition, FilterOperator, NodeFilter, StorageReadResult


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

    def as_dict(self) -> dict:
        return asdict(self)


COMMUNITY_DEFAULTS = CommunityDefaults()


def _validate_query_vector(value) -> list[float]:
    """Validate the query embedding without changing its magnitude or direction."""
    try:
        if not isinstance(value, (list, tuple, np.ndarray)):
            raise ValueError
        if any(not isinstance(x, Real) or isinstance(x, (bool, np.bool_)) for x in value):
            raise ValueError
        vector = np.asarray(value, dtype=np.float64)
        if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all() or not vector.any():
            raise ValueError
        return vector.tolist()
    except (TypeError, ValueError, OverflowError) as exc:
        raise CommunityRetrievalError("invalid_vector") from exc


def _evidence_tokens(records: list[CommunityEvidence]) -> int:
    """Sum each node's text tokens, excluding JSON syntax and metadata."""
    tokenizer = tiktoken.get_encoding(COMMUNITY_DEFAULTS.budget_tokenizer)
    return sum(len(tokenizer.encode(record.text, disallowed_special=())) for record in records)


def _pack_evidence(ranked: list[tuple[float, dict]]) -> tuple[list[CommunityEvidence], int, list[dict]]:
    """Deduplicate bodies ignoring whitespace, then fit whole nodes below the budget."""
    policy = COMMUNITY_DEFAULTS
    records: list[CommunityEvidence] = []
    actions: list[dict] = []
    seen: set[str] = set()
    for _, scene in ranked:
        if len(records) >= policy.evidence_top_k:
            break
        text = scene["content"]
        key = "".join(text.split())
        if not key or key in seen:
            actions.append({"node_id": scene["id"], "action": "duplicate" if key else "empty_body"})
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
    def __init__(self, *, recall_candidates: Callable[..., Awaitable[StorageReadResult]], end_user_id: str,
                 embedder, model_config_id: str, model_name: str, operation_id: str,
                 semantic_degraded: bool = False):
        if not end_user_id:
            raise CommunityRetrievalError("invalid_ownership")
        self.recall_candidates = recall_candidates
        self.end_user_id = end_user_id
        self.embedder = embedder
        self.operation_id = operation_id
        self.semantic_degraded = semantic_degraded
        self.trace = CommunityRetrievalTrace(
            parameters=COMMUNITY_DEFAULTS.as_dict(), model_config_id=model_config_id, model_name=model_name,
            semantic_degraded=semantic_degraded,
        )

    async def search(self, query: str, *, compatibility_parameters: dict) -> MemorySearchResult:
        self.trace.ignored_compatibility_parameters = compatibility_parameters
        try:
            with timed_stage(self.trace, "total"):
                async with asyncio.timeout(COMMUNITY_DEFAULTS.retrieval_timeout_seconds):
                    result = await self._search(query)
            self.trace.stage_duration_ms["total"] += self.trace.stage_duration_ms.get("initialization", 0)
            self.trace.status = "completed"
            return result
        except asyncio.CancelledError:
            self.trace.status, self.trace.error_reason = "failed", "cancelled"
            raise
        except Exception as exc:
            self.trace.status = "failed"
            self.trace.error_reason = (
                str(exc) if isinstance(exc, CommunityRetrievalError)
                else "scan_timeout" if isinstance(exc, TimeoutError)
                else "dependency_failed"
            )
            raise
        finally:
            log_community_trace(self.trace, operation_id=self.operation_id, end_user_id=self.end_user_id)

    def _validate_node(self, node: dict, *, scene: bool, community_id: str | None = None) -> None:
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

    async def _recall(self, query: str, *, community_id: str | None = None) -> tuple[list[tuple[float, dict]], int]:
        fulltext = self.semantic_degraded
        scene = community_id is not None
        label = MemoryNodeType.SCENE_SUMMARY if scene else MemoryNodeType.SCENE_COMMUNITY
        policy = COMMUNITY_DEFAULTS
        limit = policy.member_top_k if scene else policy.community_top_k
        threshold = policy.scene_min_cosine if scene else policy.community_min_cosine
        filters = [
            FilterCondition(field="end_user_id", value=self.end_user_id),
            FilterCondition(field="delete_at", operator=FilterOperator.EXISTS, value=False),
        ]
        if scene:
            filters.extend([
                FilterCondition(field="scene_community_id", value=community_id),
                FilterCondition(field="community_status", value="ACTIVE"),
                FilterCondition(field="community_eligibility", value="ELIGIBLE"),
            ])
        value: list[float] | str = query
        if not fulltext:
            previous_embedding_ms = self.trace.stage_duration_ms.get("embedding", 0)
            try:
                with timed_stage(self.trace, "embedding"):
                    self.trace.embedding_calls += 1
                    value = _validate_query_vector(await self.embedder.aembed_query(query))
                    self.trace.vector_dimension = len(value)
            finally:
                self.trace.stage_duration_ms["embedding"] += previous_embedding_ms
        self.trace.database_requests += 1
        result = await self.recall_candidates(
            value,
            node_filters={label: NodeFilter.all_of(*filters)},
            limit=limit,
        )
        self.trace.backend = result.backend
        candidates = []
        for item in result.items:
            node, score = item.data, item.data.get("score")
            self._validate_node(node, scene=scene, community_id=community_id)
            if item.label != label:
                raise CommunityRetrievalError("invalid_node_id")
            # Score parsing and dependency failures use the provider's default behavior.
            if not fulltext and score < threshold - 1e-6:
                continue
            candidates.append((float(score), node))
        # Shared providers expose a bounded candidate pool, not an exact match total.
        return sorted(candidates, key=lambda item: (-item[0], item[1]["id"])), len(result.items)

    def _result(self, *, memories=None, content="", reason=None) -> MemorySearchResult:
        self.trace.empty_reason = reason
        if not memories:
            self.trace.final_tokens = 0
        return MemorySearchResult(
            memories=memories or [], content_str=content,
            execution_trace=RetrievalExecutionTrace(
                keyword_status="completed" if self.semantic_degraded else "skipped",
                semantic_status="degraded" if self.semantic_degraded else "completed",
                keyword_hit_count=len(self.trace.merged_ranking) if self.semantic_degraded else 0,
                semantic_hit_count=0 if self.semantic_degraded else len(self.trace.merged_ranking),
                degraded_reasons=["model_not_ready"] if self.semantic_degraded else [],
                raw_hit_count=sum(len(item["candidates"]) for item in self.trace.members),
                merged_count=len(memories or []), limit=COMMUNITY_DEFAULTS.evidence_top_k,
            ),
        )

    async def _search(self, query: str) -> MemorySearchResult:
        with timed_stage(self.trace, "community_selection"):
            selected, recalled_community_count = await self._recall(query)
            self.trace.recalled_community_count = recalled_community_count
        self.trace.selected_communities = [
            {"id": n["id"], "score": s, "summary": n["summary"]} for s, n in selected
        ]
        if not selected:
            return self._result(reason="no_community_hit")
        merged: dict[str, tuple[float, dict]] = {}
        with timed_stage(self.trace, "scene_selection"):
            for _, community in selected:
                candidates, recalled_scene_count = await self._recall(query, community_id=community["id"])
                self.trace.members.append({
                    "community_id": community["id"], "matched_scene_count": None,
                    "recalled_scene_count": recalled_scene_count,
                    "candidates": [{"id": n["id"], "score": s} for s, n in candidates],
                })
                for score, node in candidates:
                    merged[node["id"]] = (score, node)
        ranked = sorted(merged.values(), key=lambda item: (-item[0], item[1]["id"]))
        self.trace.merged_ranking = [{"id": n["id"], "score": s} for s, n in ranked]
        if not ranked:
            return self._result(reason="no_scene_hit_in_communities")
        with timed_stage(self.trace, "evidence_packing"):
            records, tokens, actions = _pack_evidence(ranked)
            self.trace.final_tokens, self.trace.packing_actions = tokens, actions
            self.trace.evidence_ids = [r.node_id for r in records]
        if not records:
            return self._result(reason="no_valid_evidence")
        memories = [Memory(
            id=record.node_id, source=MemoryNodeType.SCENE_SUMMARY,
            score=merged[record.node_id][0], content=record.text, query=query,
        ) for record in records]
        return self._result(memories=memories, content="\n".join(record.text for record in records))
