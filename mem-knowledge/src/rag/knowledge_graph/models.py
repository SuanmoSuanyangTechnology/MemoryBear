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
class GraphTaskState:
    knowledge_id: str
    workspace_id: str
    pipeline: Any
    graph_enabled: bool
    document_active: bool | None
    active_document_ids: tuple[str, ...]


__all__ = [
    "AffectedProjectionKeys",
    "EntityEvidence",
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionBatch",
    "ExtractionResult",
    "GraphIndexRuntime",
    "GraphTaskState",
    "RelationEvidence",
    "SourceChunk",
]
