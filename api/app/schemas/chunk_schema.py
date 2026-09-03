import uuid
from enum import StrEnum
from typing import Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.rag.models.chunk import QAChunk
from app.integrations.knowledge.contracts import (
    KnowledgeRetrievalSource as KnowledgeRetrievalSource,
)
from app.schemas.knowledge_metadata_schema import FilterGroup, MetadataFilterMode
from app.schemas.rerank_schema import RerankMode, RerankWeights


class RetrieveType(StrEnum):
    """Retrieval type enumeration"""
    PARTICIPLE = "participle"
    SEMANTIC = "semantic"
    Graph = "graph"
    HYBRID = "hybrid"


class KnowledgeBaseConfig(BaseModel):
    kb_id: uuid.UUID = Field(..., description="Knowledge base ID")
    similarity_threshold: float = Field(default=0.2, ge=0, le=1, description="Knowledge base similarity threshold")
    vector_similarity_weight: float | None = Field(default=0.3, ge=0, le=1, description="Knowledge base vector similarity weight（语义/混合/图谱检索使用，分词检索不使用）")
    rerank_score_threshold: float | None = Field(default=None, ge=0, le=1, description="Knowledge base rerank score threshold")
    top_k: int = Field(default=4, ge=1, le=100, description="Knowledge base top k")
    retrieve_type: RetrieveType = Field(default=RetrieveType.PARTICIPLE, description="Retrieve type")
    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None
    enable_graph_retrieval: int | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "Whether this knowledge base adds the Evidence Graph channel "
            "during hybrid retrieval. Falls back to the request value when omitted."
        ),
    )


class ChunkType(StrEnum):
    """Chunk type enumeration"""
    CHUNK = "chunk"
    PARENT = "parent"
    CHILD = "child"
    QA = "qa"


class ChunkCreate(BaseModel):
    content: Union[str, QAChunk] = Field(
        description="Content can be either a string or a QAChunk object"
    )
    chunk_type: ChunkType = Field(default=ChunkType.CHUNK, description="chunk 类型")
    parent_id: str | None = Field(default=None, description="父块 doc_id（仅 child 类型必填）")

    @property
    def chunk_content(self) -> str:
        """Get the actual content string regardless of input type"""
        if isinstance(self.content, QAChunk):
            return self.content.question  # QA 模式下 page_content 存 question
        return self.content

    @property
    def is_qa(self) -> bool:
        return isinstance(self.content, QAChunk) or self.chunk_type == ChunkType.QA

    @property
    def qa_metadata(self) -> dict:
        """返回 QA 相关的 metadata 字段"""
        if isinstance(self.content, QAChunk):
            return {
                "chunk_type": "qa",
                "question": self.content.question,
                "answer": self.content.answer,
            }
        return {}

    @property
    def type_metadata(self) -> dict:
        """根据 chunk_type 返回对应的 metadata 字段"""
        meta = {"chunk_type": self.chunk_type.value}
        if self.chunk_type == ChunkType.CHILD and self.parent_id:
            meta["parent_id"] = self.parent_id
        return meta


class ChunkUpdate(BaseModel):
    content: Union[str, QAChunk] = Field(
        description="Content can be either a string or a QAChunk object"
    )

    @property
    def chunk_content(self) -> str:
        """Get the actual content string regardless of input type"""
        if isinstance(self.content, QAChunk):
            return self.content.question  # QA 模式下 page_content 存 question
        return self.content

    @property
    def is_qa(self) -> bool:
        return isinstance(self.content, QAChunk)

    @property
    def qa_metadata(self) -> dict:
        """返回 QA 相关的 metadata 字段"""
        if isinstance(self.content, QAChunk):
            return {
                "chunk_type": "qa",
                "question": self.content.question,
                "answer": self.content.answer,
            }
        return {}


class ChunkRetrieve(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    kb_ids: list[uuid.UUID] = Field(default_factory=list)
    ex_ids: list[str] | None = Field(None)
    knowledge_bases: list[KnowledgeBaseConfig] = Field(default_factory=list)
    file_names_filter: list[str] | None = Field(None)
    similarity_threshold: float | None = Field(None)
    vector_similarity_weight: float | None = Field(None)
    top_k: int | None = Field(20, ge=1, le=100)
    top_n: int | None = Field(20, ge=1, le=100)
    retrieve_type: RetrieveType | None = Field(None)
    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None
    enable_graph_retrieval: int = Field(
        0,
        ge=0,
        le=1,
        description="Whether to add the graph retrieval route to hybrid retrieval. 1 enables it.",
    )
    rerank_score_threshold: float | None = Field(None, ge=0, le=1)
    metadata_filters: list[FilterGroup] | None = Field(None, description="filter condition groups")
    metadata_filter_mode: MetadataFilterMode = Field(MetadataFilterMode.MANUAL, description="filter mode")

    @model_validator(mode="after")
    def resolve_top_n(self):
        if not self.kb_ids and not self.ex_ids and not self.knowledge_bases:
            raise ValueError("kb_ids, ex_ids and knowledge_bases cannot all be empty")
        top_k = self.top_k or 20
        if self.top_n is None or "top_n" not in self.model_fields_set:
            self.top_n = max(top_k, 20)
        elif self.top_n < top_k:
            raise ValueError("top_n must be greater than or equal to top_k")
        return self


class ChunkBatchCreate(BaseModel):
    """批量创建 chunk"""
    items: list[ChunkCreate] = Field(..., min_length=1, description="chunk 列表")
