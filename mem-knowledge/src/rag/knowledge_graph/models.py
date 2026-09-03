"""Data contracts for the Evidence Graph indexing pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from redbear_model import ResolvedModelConfig


class SourceChunk(BaseModel):
    source_chunk_id: str
    document_id: str
    page_content: str
    sort_id: int
    chunk_type: str = "chunk"
    parent_id: str | None = None


class ExtractionBatch(BaseModel):
    text: str
    source_chunk_ids: tuple[str, ...]


class ExtractedEntity(BaseModel):
    ref: str
    name: str
    entity_type: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedRelation(BaseModel):
    from_ref: str
    to_ref: str
    predicate: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    directed: bool = True
    source_chunk_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


class GraphQueryPlan(BaseModel):
    low_level_keywords: list[str] = Field(default_factory=list)
    high_level_keywords: list[str] = Field(default_factory=list)


class EntityEvidence(BaseModel):
    id: str
    kb_id: str
    document_id: str
    source_chunk_id: str
    entity_key: str
    entity_name: str
    entity_type: str
    description: str
    aliases: tuple[str, ...] = ()
    confidence: float


class RelationEvidence(BaseModel):
    id: str
    kb_id: str
    document_id: str
    source_chunk_id: str
    relation_key: str
    from_entity_key: str
    from_entity_name: str
    to_entity_key: str
    to_entity_name: str
    predicate: str
    description: str
    keywords: tuple[str, ...] = ()
    directed: bool
    confidence: float


class AffectedProjectionKeys(BaseModel):
    entity_keys: tuple[str, ...]
    relation_keys: tuple[str, ...]


class EntityProjectionHit(BaseModel):
    entity_key: str
    entity_name: str
    entity_type: str = ""
    description: str = ""
    aliases: tuple[str, ...] = ()
    score: float
    degree: int = 0
    evidence_count: int = 0
    document_count: int = 0


class RelationProjectionHit(BaseModel):
    relation_key: str
    from_entity_key: str
    from_entity_name: str = ""
    to_entity_key: str
    to_entity_name: str = ""
    predicate: str = ""
    label: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    directed: bool = True
    score: float
    evidence_count: int = 0
    document_count: int = 0
    endpoint_degree: int = 0


class GraphEvidenceHit(BaseModel):
    evidence_id: str = ""
    source_chunk_id: str
    document_id: str
    score: float
    entity_key: str | None = None
    relation_key: str | None = None
    entity_name: str | None = None
    relation_label: str | None = None


class SourceChunkVectorHit(BaseModel):
    source_chunk_id: str
    score: float


class GraphRetrievalResult(BaseModel):
    chunks: list[Any] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)


class ProjectionEvidenceGroup(BaseModel):
    projection_type: str
    projection_key: str
    evidence: tuple[GraphEvidenceHit, ...] = ()


@dataclass(frozen=True)
class GraphIndexRuntime:
    knowledge_id: str
    workspace_id: str
    graph_index_name: str
    chunk_index_name: str
    entity_types: tuple[str, ...]
    scene_name: str
    llm: ResolvedModelConfig
    embedding: ResolvedModelConfig


@dataclass(frozen=True)
class GraphRetrievalRequest:
    query: str
    runtime: GraphIndexRuntime
    allowed_document_ids: tuple[str, ...] | None
    file_names: tuple[str, ...]
    max_candidates: int
    entity_top_n: int = 40
    relation_top_n: int = 40
    neighbor_top_n: int = 24
    entity_similarity_threshold: float = 0.20
    relation_similarity_threshold: float = 0.20
    related_chunk_number: int = 5
    max_paths_per_chunk: int = 6


@dataclass(frozen=True)
class GraphTaskState:
    knowledge_id: str
    workspace_id: str
    pipeline: Any
    graph_enabled: bool
    document_active: bool | None
    active_document_ids: tuple[str, ...]


__all__ = [
    "AffectedProjectionKeys",
    "EntityProjectionHit",
    "EntityEvidence",
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionBatch",
    "ExtractionResult",
    "GraphIndexRuntime",
    "GraphEvidenceHit",
    "GraphQueryPlan",
    "GraphRetrievalRequest",
    "GraphRetrievalResult",
    "GraphTaskState",
    "ProjectionEvidenceGroup",
    "RelationProjectionHit",
    "RelationEvidence",
    "SourceChunk",
    "SourceChunkVectorHit",
]
