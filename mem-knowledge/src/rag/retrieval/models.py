"""Immutable retrieval snapshots copied from the legacy retrieval service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from ...api.schemas.chunk import RetrieveType
from ...api.schemas.rerank import RerankMode
from ..knowledge_graph.config import GraphPipeline
from ..models.chunk import DocumentChunk


@dataclass(frozen=True)
class ModelRuntimeSnapshot:
    model_name: str
    provider: str
    api_key: str = field(repr=False)
    api_base: str | None = None
    capability: tuple[str, ...] = ()
    is_omni: bool = False
    model_type: str | None = None
    resolved: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class RerankWeightsSnapshot:
    semantic_weight: float
    participle_weight: float


@dataclass(frozen=True)
class RerankPlan:
    mode: RerankMode
    weights: RerankWeightsSnapshot
    model: ModelRuntimeSnapshot | None
    compatibility_fallback: bool


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk: DocumentChunk
    knowledge_id: uuid.UUID
    semantic_score: float | None
    participle_score: float | None
    graph_score: float | None
    final_score: float | None
    arrival_index: int

    def with_final_score(self, score: float) -> RetrievalCandidate:
        return replace(self, final_score=score)


@dataclass(frozen=True)
class RetrievalParams:
    similarity_threshold: float
    vector_similarity_weight: float | None
    top_k: int
    top_n: int
    retrieve_type: RetrieveType
    rerank_score_threshold: float = 0.1
    enable_graph_retrieval: bool = False
    local_rerank: RerankPlan | None = None


@dataclass(frozen=True)
class RetrievalTarget:
    knowledge_id: uuid.UUID
    workspace_id: uuid.UUID
    index_name: str
    params: RetrievalParams
    embedding: ModelRuntimeSnapshot
    reranker: ModelRuntimeSnapshot | None


@dataclass(frozen=True)
class RetrievalSearchOptions:
    indices: str
    top_k: int
    score_threshold: float | None
    file_names_filter: tuple[str, ...]
    document_ids_include: tuple[str, ...] | None
    knn_num_candidates: int | None = None


@dataclass
class RetrievalTimings:
    """Mutable request-local phase timings for retrieval observability."""

    db_snapshot_ms: int = 0
    metadata_llm_ms: int = 0
    metadata_query_ms: int = 0
    embedding_ms: int = 0
    es_vector_ms: int = 0
    es_fulltext_ms: int = 0
    parent_resolution_ms: int = 0
    local_rerank_ms: int = 0
    global_rerank_ms: int = 0
    graph_wait_ms: int = 0
    graph_ms: int = 0

    def as_log_fields(self) -> dict[str, int]:
        return {
            "db_snapshot_ms": self.db_snapshot_ms,
            "metadata_llm_ms": self.metadata_llm_ms,
            "metadata_query_ms": self.metadata_query_ms,
            "embedding_ms": self.embedding_ms,
            "es_vector_ms": self.es_vector_ms,
            "es_fulltext_ms": self.es_fulltext_ms,
            "parent_resolution_ms": self.parent_resolution_ms,
            "local_rerank_ms": self.local_rerank_ms,
            "global_rerank_ms": self.global_rerank_ms,
            "graph_wait_ms": self.graph_wait_ms,
            "graph_ms": self.graph_ms,
        }


@dataclass(frozen=True)
class GraphTargetSnapshot:
    knowledge_id: uuid.UUID
    workspace_id: uuid.UUID
    chunk_index_name: str
    graph_index_name: str
    pipeline: GraphPipeline
    llm: ModelRuntimeSnapshot
    embedding: ModelRuntimeSnapshot


@dataclass(frozen=True)
class GraphRetrievalSnapshot:
    query: str
    pipeline: GraphPipeline
    targets: tuple[GraphTargetSnapshot, ...]
    timings: RetrievalTimings | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError("graph retrieval snapshot requires at least one target")
        if any(target.pipeline is not self.pipeline for target in self.targets):
            raise ValueError("graph retrieval targets must use the selected pipeline")

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return tuple(str(target.workspace_id) for target in self.targets)

    @property
    def knowledge_ids(self) -> tuple[str, ...]:
        return tuple(str(target.knowledge_id) for target in self.targets)

    @property
    def llm(self) -> ModelRuntimeSnapshot:
        return self.targets[0].llm

    @property
    def embedding(self) -> ModelRuntimeSnapshot:
        return self.targets[0].embedding


@dataclass(frozen=True)
class RetrievalPreparation:
    targets: tuple[RetrievalTarget, ...]
    tenant_id: uuid.UUID | None
    metadata_defs_by_kb: dict[uuid.UUID, dict[str, dict[str, Any]]]
    common_metadata_defs: dict[str, dict[str, Any]]
    metadata_llm: ModelRuntimeSnapshot | None
    graph: GraphRetrievalSnapshot | None
    request_reranker: ModelRuntimeSnapshot | None = None
    global_rerank: RerankPlan | None = None


@dataclass(frozen=True)
class TargetRetrievalResult:
    candidates: tuple[RetrievalCandidate, ...]
    entities: tuple[Any, ...] = ()
    relationships: tuple[Any, ...] = ()


__all__ = [
    "GraphRetrievalSnapshot",
    "GraphTargetSnapshot",
    "ModelRuntimeSnapshot",
    "RerankPlan",
    "RerankWeightsSnapshot",
    "RetrievalCandidate",
    "RetrievalParams",
    "RetrievalPreparation",
    "RetrievalSearchOptions",
    "RetrievalTarget",
    "RetrievalTimings",
    "TargetRetrievalResult",
]
