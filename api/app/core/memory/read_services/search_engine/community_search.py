"""Community and Scene retrieval, using full text while embeddings are rebuilding."""

import asyncio
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
    def __init__(self, *, repository, embedder, model_config_id: str, model_name: str, operation_id: str,
                 semantic_degraded: bool = False):
        self.repository = repository
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
            self.trace.database_requests = self.repository.database_requests
            self.trace.skipped_hits = list(self.repository.skipped_hits)
            log_community_trace(self.trace, operation_id=self.operation_id, end_user_id=self.repository.end_user_id)

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
        query_vector = None
        if not self.semantic_degraded:
            with timed_stage(self.trace, "embedding"):
                self.trace.embedding_calls += 1
                query_vector = _validate_query_vector(await self.embedder.aembed_query(query))
                self.trace.vector_dimension = len(query_vector)
        with timed_stage(self.trace, "community_selection"):
            selected, matched_community_count = (
                await self.repository.communities_fulltext(query) if self.semantic_degraded
                else await self.repository.communities(query_vector)
            )
            self.trace.matched_community_count = matched_community_count
        self.trace.selected_communities = [
            {"id": n["id"], "score": s, "summary": n["summary"]} for s, n in selected
        ]
        if not selected:
            return self._result(reason="no_community_hit")
        merged: dict[str, tuple[float, dict]] = {}
        with timed_stage(self.trace, "scene_selection"):
            for _, community in selected:
                candidates, matched_scene_count = (
                    await self.repository.members_fulltext(community, query) if self.semantic_degraded
                    else await self.repository.members(community, query_vector)
                )
                self.trace.members.append({
                    "community_id": community["id"], "matched_scene_count": matched_scene_count,
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
