from pydantic import BaseModel, Field
import uuid
from enum import StrEnum
from app.core.rag.models.chunk import QAChunk
from typing import Union, Any


class RetrieveType(StrEnum):
    """Retrieval type enumeration"""
    PARTICIPLE = "participle"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    Graph = "graph"


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


class FilterCondition(BaseModel):
    field: str = Field(..., description="元数据字段名")
    operator: str = Field(..., description="操作符")
    value: Any | None = Field(None, description="值")


class FilterGroup(BaseModel):
    conditions: list[FilterCondition] = Field(..., description="条件列表")
    logic: str = Field("AND", description="组内逻辑: AND | OR")


class MetadataFilterMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class ChunkRetrieve(BaseModel):
    query: str
    kb_ids: list[uuid.UUID]
    ex_ids: list[str] | None = Field(None)
    file_names_filter: list[str] | None = Field(None)
    similarity_threshold: float | None = Field(None)
    vector_similarity_weight: float | None = Field(None)
    top_k: int | None = Field(100, ge=1, le=100)
    retrieve_type: RetrieveType | None = Field(None)
    rerank_score_threshold: float | None = Field(None, ge=0, le=1)

    # === 新增：元数据过滤 ===
    metadata_filters: list[FilterGroup] | None = Field(None, description="元数据过滤条件")
    metadata_filter_mode: MetadataFilterMode = Field(MetadataFilterMode.MANUAL, description="过滤模式")


class ChunkBatchCreate(BaseModel):
    """批量创建 chunk"""
    items: list[ChunkCreate] = Field(..., min_length=1, description="chunk 列表")
