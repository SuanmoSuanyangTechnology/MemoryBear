"""Domain models of the memory-read core.

The shapes here are value objects: they carry no database session, no driver
object and no FastAPI primitive. Adapters translate storage rows into these
objects, application use cases compose them, and the transport layer encodes
them back into the legacy wire contract.

``MemoryItem`` / ``ReadResult`` intentionally mirror the host's
``Memory`` / ``MemorySearchResult`` field names and serialization behavior so a
migrated payload is byte-identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_serializer

from src.core.domain.enums import (
    BranchStatus,
    MemorySource,
    SearchStrategy,
    StorageType,
)

RetrievalType = Literal["keyword", "semantic", "hybrid", "provider"]
RankBasis = Literal[
    "keyword_score",
    "fusion_score",
    "source_adjusted_score",
    "rerank_score",
    "rerank_truncated",
    "provider_score",
    "input_order",
]
EmptyCommunityReason = Literal[
    "no_community_hit", "no_scene_hit_in_communities", "no_valid_evidence"
]


# ────────────────────────────────────────────────────────────────
# Retrieval traces (observability values, excluded from public payloads)
# ────────────────────────────────────────────────────────────────


def finite_or_none(value: Any) -> float | None:
    """Keep the difference between 0 and "missing" while dropping NaN/Infinity."""

    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class RetrievalScoreTrace(BaseModel):
    """Score composition of one retrieved candidate."""

    retrieval_type: RetrievalType
    keyword_score: float | None = None
    semantic_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None
    final_score: float
    rank_basis: RankBasis
    backend: Literal["neo4j", "rag"]
    node_type: str
    node_id: str
    matched_queries: list[str] = Field(default_factory=list)


class RetrievalExecutionTrace(BaseModel):
    """Request-level branch status; only used by validation projections."""

    original_query: str = ""
    processed_query: str = ""
    search_switch: str = ""
    backend: Literal["neo4j", "rag"] = "neo4j"
    limit: int = 10
    keyword_status: BranchStatus = "skipped"
    semantic_status: BranchStatus = "skipped"
    rerank_status: BranchStatus = "skipped"
    keyword_hit_count: int = 0
    semantic_hit_count: int = 0
    raw_hit_count: int = 0
    merged_count: int = 0
    degraded_reasons: list[str] = Field(default_factory=list)


def normalized_keyword_score(raw_score: Any, threshold: float) -> float:
    """Diagnostic normalization only; it never drives the real ordering."""

    score = finite_or_none(raw_score) or 0.0
    return 1 / (1 + math.exp(-(score - threshold) / 2)) if score else 0.0


def diagnostic_fusion_score(
    keyword_score: float, semantic_score: float, alpha: float
) -> float:
    """Diagnostic fusion score only; the real formula lives in the retriever."""

    base = alpha * semantic_score + (1 - alpha) * keyword_score
    return base + min(1 - base, 0.1 * keyword_score * semantic_score)


def build_score_trace(
    *,
    node_id: str,
    node_type: str,
    final_score: Any,
    rank_basis: RankBasis,
    keyword_hit: bool = False,
    semantic_hit: bool = False,
    keyword_score: Any = None,
    semantic_score: Any = None,
    fusion_score: Any = None,
    rerank_score: Any = None,
    backend: Literal["neo4j", "rag"] = "neo4j",
    matched_queries: Any = (),
) -> RetrievalScoreTrace:
    """Build the per-candidate trace from an already-decided result."""

    if backend == "rag":
        retrieval_type: RetrievalType = "provider"
    elif keyword_hit and semantic_hit:
        retrieval_type = "hybrid"
    elif semantic_hit:
        retrieval_type = "semantic"
    else:
        retrieval_type = "keyword"

    final = finite_or_none(final_score) or 0.0
    queries = list(dict.fromkeys(str(query) for query in matched_queries if str(query)))
    return RetrievalScoreTrace(
        retrieval_type=retrieval_type,
        keyword_score=finite_or_none(keyword_score) if keyword_hit else None,
        semantic_score=finite_or_none(semantic_score) if semantic_hit else None,
        fusion_score=finite_or_none(fusion_score),
        rerank_score=finite_or_none(rerank_score),
        final_score=final,
        rank_basis=rank_basis,
        backend=backend,
        node_type=node_type,
        node_id=str(node_id),
        matched_queries=queries,
    )


class CommunityEvidence(BaseModel):
    """One evidence node packed for the community answer prompt."""

    node_id: str
    node_type: Literal["SceneSummary"] = "SceneSummary"
    text: str


class CommunityRetrievalTrace(BaseModel):
    """Community retrieval diagnostics, projected by the streaming face."""

    parameters: dict[str, Any] = Field(default_factory=dict)
    model_config_id: str = ""
    model_name: str = ""
    vector_dimension: int = 0
    embedding_calls: int = 0
    semantic_degraded: bool = False
    database_requests: int = Field(
        default=0,
        description=(
            "Logical storage recall calls; providers may issue multiple physical "
            "database requests."
        ),
    )
    backend: str | None = None
    matched_community_count: int | None = Field(
        default=None,
        description="Exact match total, unavailable from shared bounded candidate recall.",
    )
    recalled_community_count: int = Field(
        default=0,
        description="Community candidates returned by storage before local score validation.",
    )
    selected_communities: list[dict[str, Any]] = Field(default_factory=list)
    members: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-community recalled_scene_count, optional exact matched_scene_count "
            "and selected candidates."
        ),
    )
    merged_ranking: list[dict[str, Any]] = Field(default_factory=list)
    packing_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    final_tokens: int = 0
    empty_reason: EmptyCommunityReason | None = None
    ignored_compatibility_parameters: dict[str, Any] = Field(default_factory=dict)
    stage_duration_ms: dict[str, float] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed"] = "running"
    error_reason: str | None = None


# ────────────────────────────────────────────────────────────────
# Retrieval result value objects
# ────────────────────────────────────────────────────────────────


class MemoryItem(BaseModel):
    """One retrieved memory item."""

    source: MemorySource | str = Field(...)
    score: float = Field(default=0.0)
    content: str = Field(default="")
    data: dict[str, Any] = Field(default_factory=dict)
    query: str = Field(...)
    id: str = Field(...)
    retrieval_trace: RetrievalScoreTrace | None = Field(default=None, exclude=True)

    @field_serializer("source")
    def serialize_source(self, value: MemorySource | str) -> str:
        return value.value if isinstance(value, MemorySource) else str(value)

    @field_serializer("score")
    def serialize_score(self, value: float) -> float:
        return round(value, 2)


class RelationItem(BaseModel):
    """One extracted entity-to-entity relation."""

    source: str = ""
    relation: str = ""
    target: str = ""
    target_desc: str = ""
    target_id: str = ""

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        return self.source, self.relation, self.target


class QuestionSplit(BaseModel):
    """Structured output of the problem-splitting prompt."""

    questions: list[Any] = Field(default_factory=list)


class RetrievalSummary(BaseModel):
    """Structured output of the retrieval-summary prompt.

    The compressed context is carried in a single field so the model call can use
    the provider's structured-output channel (``call_structured``) while the
    prompt/repair fallback still yields a validated object.
    """

    summary: str = Field(min_length=1)


class EntityPair(BaseModel):
    source_id: str = ""
    target_id: str = ""


class RelationPairs(BaseModel):
    pairs: list[EntityPair] = Field(default_factory=list)


def build_relation_content(relation: RelationItem) -> str:
    """Render one relation the way the host appends it to retrieval content."""

    parts = [f"{relation.source} {relation.relation} {relation.target}"]
    if relation.target_desc:
        parts.append(relation.target_desc)
    return " - ".join(parts)


class ReadResult(BaseModel):
    """Aggregated retrieval result for one query or strategy."""

    memories: list[MemoryItem]
    relations: list[RelationItem] = Field(default_factory=list)
    content_str: str = Field(default="")
    community_trace: CommunityRetrievalTrace | None = Field(default=None, exclude=True)
    execution_trace: RetrievalExecutionTrace | None = Field(default=None, exclude=True)
    dedup_summary: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @property
    def content(self) -> str:
        parts: list[str] = []
        if self.content_str:
            parts.append(self.content_str)
        else:
            parts.append("\n".join(memory.content for memory in self.memories))
            if self.relations:
                parts.append("\n".join(build_relation_content(rel) for rel in self.relations))
        return "\n".join(parts)

    @computed_field
    @property
    def count(self) -> int:
        return len(self.memories)

    def filter(self, score_threshold: float) -> Self:
        self.memories = [memory for memory in self.memories if memory.score >= score_threshold]
        return self

    def _dedup_relations(self) -> None:
        seen: set[tuple[str, str, str]] = set()
        unique: list[RelationItem] = []
        for relation in self.relations:
            key = relation.dedup_key
            if key not in seen:
                seen.add(key)
                unique.append(relation)
        self.relations = unique

    def __add__(self, other: ReadResult) -> ReadResult:
        if not isinstance(other, ReadResult):
            raise TypeError(f"unsupported operand type({type(other)}) for +")

        merged = ReadResult(memories=list(self.memories))
        ids = {memory.id for memory in merged.memories}
        for memory in other.memories:
            if memory.id not in ids:
                merged.memories.append(memory)
                ids.add(memory.id)

        merged.relations = list(self.relations)
        seen_keys = {relation.dedup_key for relation in merged.relations}
        for relation in other.relations:
            key = relation.dedup_key
            if key not in seen_keys:
                merged.relations.append(relation)
                seen_keys.add(key)

        merged.content_str = other.content_str or self.content_str
        return merged


# ────────────────────────────────────────────────────────────────
# Use-case inputs and outputs
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReadPrincipal:
    """Caller identity resolved by the transport layer (never by the core)."""

    workspace_id: UUID
    #: ``None`` for channels that carry no credential-bound tenant (the
    #: application-service callback): the config's tenant is used verbatim.
    tenant_id: UUID | None = None
    actor_id: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class ReadConfig:
    """The slice of ``memory_config`` + workspace a read needs."""

    config_id: UUID
    config_name: str
    workspace_id: UUID
    workspace_name: str
    tenant_id: UUID
    llm_model_id: UUID | None = None
    llm_model_name: str | None = None
    embedding_model_id: UUID | None = None
    embedding_model_name: str | None = None
    rerank_model_id: UUID | None = None
    rerank_model_name: str | None = None
    vision_model_id: UUID | None = None
    vision_model_name: str | None = None
    audio_model_id: UUID | None = None
    audio_model_name: str | None = None
    video_model_id: UUID | None = None
    video_model_name: str | None = None


@dataclass(frozen=True, slots=True)
class ReadContext:
    """Everything one retrieval run needs besides the query itself."""

    end_user_id: str
    workspace_id: UUID
    tenant_id: UUID
    config: ReadConfig | None
    storage_type: StorageType = StorageType.NEO4J
    language: str = "zh"
    draft: bool = False
    conversation_id: str | None = None
    app_id: UUID | None = None
    user_rag_memory_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReadOptions:
    """Strategy-independent retrieval options."""

    limit: int = 10
    includes: tuple[str, ...] | None = None
    skip_summary: bool = False
    enable_rerank: bool = False
    record_display: bool = False
    include_metadata: bool = True


@dataclass(frozen=True, slots=True)
class ReadContextOverrides:
    """Per-caller overrides of what the read context factory resolves.

    Only the application-service callback names these facts explicitly; every
    other face keeps the workspace-level resolution. ``None`` means "resolve as
    usual", so an unset field never changes the existing faces.
    """

    config_id: UUID | None = None
    storage_type: str | None = None
    draft: bool = False
    conversation_id: str | None = None
    app_id: UUID | None = None
    user_rag_memory_id: str | None = None


@dataclass(frozen=True, slots=True)
class SyncReadCommand:
    """Non-streaming read: one host, or a set of hosts read concurrently."""

    message: str
    search_switch: SearchStrategy
    end_user_ids: tuple[str, ...]
    enable_rerank: bool = False


@dataclass(frozen=True, slots=True)
class StreamReadCommand:
    """Streaming read: single host plus the conversation session."""

    message: str
    search_switch: SearchStrategy
    end_user_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class ReadOutcome:
    """One answer plus the legacy ``intermediate_outputs`` projection.

    ``end_user_id`` is the merge-routed host the read actually ran for; the
    transport needs it to identify the terminal-user profile item.
    """

    answer: str
    intermediate_outputs: list[dict[str, Any]]
    result: ReadResult
    end_user_id: str = ""


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """A transport-agnostic SSE event produced by the streaming use case."""

    name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EndUserRef:
    """A resolved, merge-routed end user belonging to the caller's workspace."""

    end_user_id: str


__all__ = [
    "CommunityEvidence",
    "CommunityRetrievalTrace",
    "EmptyCommunityReason",
    "EndUserRef",
    "EntityPair",
    "MemoryItem",
    "QuestionSplit",
    "RankBasis",
    "ReadConfig",
    "ReadContext",
    "ReadOptions",
    "ReadOutcome",
    "ReadPrincipal",
    "ReadResult",
    "RelationItem",
    "RelationPairs",
    "RetrievalExecutionTrace",
    "RetrievalScoreTrace",
    "RetrievalSummary",
    "RetrievalType",
    "StreamEvent",
    "StreamReadCommand",
    "SyncReadCommand",
    "build_relation_content",
    "build_score_trace",
    "diagnostic_fusion_score",
    "finite_or_none",
    "normalized_keyword_score",
]