from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.core.rag.retrieval.models import ModelRuntimeSnapshot


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
    source_chunk_ids: list[str]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedRelation(BaseModel):
    from_ref: str
    to_ref: str
    predicate: str
    description: str
    directed: bool = True
    source_chunk_ids: list[str]
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
    directed: bool
    confidence: float


class AffectedProjectionKeys(BaseModel):
    entity_keys: tuple[str, ...]
    relation_keys: tuple[str, ...]


class EntityProjectionHit(BaseModel):
    entity_key: str
    entity_name: str
    score: float


class RelationProjectionHit(BaseModel):
    relation_key: str
    from_entity_key: str
    to_entity_key: str
    label: str
    score: float


class GraphEvidenceHit(BaseModel):
    source_chunk_id: str
    document_id: str
    score: float
    entity_name: str | None = None
    relation_label: str | None = None


@dataclass(frozen=True)
class GraphIndexRuntime:
    knowledge_id: str
    workspace_id: str
    graph_index_name: str
    chunk_index_name: str
    entity_types: tuple[str, ...]
    scene_name: str
    llm: ModelRuntimeSnapshot
    embedding: ModelRuntimeSnapshot


@dataclass(frozen=True)
class GraphRetrievalRequest:
    query: str
    runtime: GraphIndexRuntime
    allowed_document_ids: tuple[str, ...] | None
    file_names: tuple[str, ...]
    entity_top_n: int
    relation_top_n: int
    neighbor_top_n: int
    evidence_per_key: int
    max_chunks_per_document: int
