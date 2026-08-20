"""Immutable retrieval snapshots copied from the legacy retrieval service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ...api.schemas.chunk import RetrieveType
from ..knowledge_graph.config import GraphPipeline


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
class RetrievalParams:
    similarity_threshold: float
    vector_similarity_weight: float | None
    top_k: int
    top_n: int
    retrieve_type: RetrieveType
    rerank_score_threshold: float = 0.1
    enable_graph_retrieval: bool = False


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

    def __post_init__(self) -> None:
        if not self.targets:
            raise ValueError("graph retrieval snapshot requires at least one target")
        if any(target.pipeline is not self.pipeline for target in self.targets):
            raise ValueError("graph retrieval targets must use the selected pipeline")


@dataclass(frozen=True)
class RetrievalPreparation:
    targets: tuple[RetrievalTarget, ...]
    tenant_id: uuid.UUID | None
    metadata_defs_by_kb: dict[uuid.UUID, dict[str, dict[str, Any]]]
    common_metadata_defs: dict[str, dict[str, Any]]
    metadata_llm: ModelRuntimeSnapshot | None
    graph: GraphRetrievalSnapshot | None
    request_reranker: ModelRuntimeSnapshot | None = None


__all__ = [
    "GraphRetrievalSnapshot",
    "GraphTargetSnapshot",
    "ModelRuntimeSnapshot",
    "RetrievalParams",
    "RetrievalPreparation",
    "RetrievalSearchOptions",
    "RetrievalTarget",
]
