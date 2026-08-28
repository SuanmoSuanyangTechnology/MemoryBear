"""Chunk and retrieval request schemas copied from the legacy API."""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...rag.models.chunk import QAChunk
from .knowledge_metadata import FilterGroup, MetadataFilterMode


class RetrieveType(StrEnum):
    PARTICIPLE = "participle"
    SEMANTIC = "semantic"
    Graph = "graph"
    HYBRID = "hybrid"


class KnowledgeRetrievalCaller(StrEnum):
    GENERAL = "general"
    EX_API = "ex_api"
    IN_API = "in_api"
    AGENT = "agent"
    WORKFLOW = "workflow"


class KnowledgeBaseConfig(BaseModel):
    kb_id: uuid.UUID
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: float | None = Field(default=0.3, ge=0, le=1)
    rerank_score_threshold: float | None = Field(default=None, ge=0, le=1)
    top_k: int = Field(default=4, ge=1, le=100)
    retrieve_type: RetrieveType = RetrieveType.PARTICIPLE
    enable_graph_retrieval: int | None = Field(default=None, ge=0, le=1)


class ChunkType(StrEnum):
    CHUNK = "chunk"
    PARENT = "parent"
    CHILD = "child"
    QA = "qa"


class ChunkCreate(BaseModel):
    content: str | QAChunk
    chunk_type: ChunkType = ChunkType.CHUNK
    parent_id: str | None = None

    @property
    def chunk_content(self) -> str:
        if isinstance(self.content, QAChunk):
            return self.content.question
        return self.content

    @property
    def is_qa(self) -> bool:
        return isinstance(self.content, QAChunk) or self.chunk_type == ChunkType.QA

    @property
    def qa_metadata(self) -> dict:
        if isinstance(self.content, QAChunk):
            return {
                "chunk_type": "qa",
                "question": self.content.question,
                "answer": self.content.answer,
            }
        return {}

    @property
    def type_metadata(self) -> dict:
        metadata = {"chunk_type": self.chunk_type.value}
        if self.chunk_type == ChunkType.CHILD and self.parent_id:
            metadata["parent_id"] = self.parent_id
        return metadata


class ChunkUpdate(BaseModel):
    content: str | QAChunk

    @property
    def chunk_content(self) -> str:
        if isinstance(self.content, QAChunk):
            return self.content.question
        return self.content

    @property
    def is_qa(self) -> bool:
        return isinstance(self.content, QAChunk)

    @property
    def qa_metadata(self) -> dict:
        if isinstance(self.content, QAChunk):
            return {
                "chunk_type": "qa",
                "question": self.content.question,
                "answer": self.content.answer,
            }
        return {}


class ChunkRetrieve(BaseModel):
    model_config = ConfigDict(extra="ignore")

    caller: KnowledgeRetrievalCaller = KnowledgeRetrievalCaller.GENERAL
    query: str
    kb_ids: list[uuid.UUID] = Field(default_factory=list)
    ex_ids: list[str] | None = None
    file_names_filter: list[str] | None = None
    similarity_threshold: float | None = None
    vector_similarity_weight: float | None = None
    top_k: int | None = Field(20, ge=1, le=100)
    top_n: int | None = Field(20, ge=1, le=100)
    retrieve_type: RetrieveType | None = None
    enable_graph_retrieval: int = Field(0, ge=0, le=1)
    rerank_score_threshold: float | None = Field(None, ge=0, le=1)
    metadata_filters: list[FilterGroup] | None = None
    metadata_filter_mode: MetadataFilterMode = MetadataFilterMode.MANUAL

    @model_validator(mode="after")
    def resolve_top_n(self) -> "ChunkRetrieve":
        if not self.kb_ids and not self.ex_ids:
            raise ValueError("kb_ids and ex_ids cannot both be empty")
        top_k = self.top_k or 20
        if self.top_n is None or "top_n" not in self.model_fields_set:
            self.top_n = max(top_k, 20)
        elif self.top_n < top_k:
            raise ValueError("top_n must be greater than or equal to top_k")
        return self


class ChunkBatchCreate(BaseModel):
    items: list[ChunkCreate] = Field(..., min_length=1)
