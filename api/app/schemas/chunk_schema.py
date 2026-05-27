from pydantic import BaseModel, Field
import uuid
from enum import StrEnum
from app.core.rag.models.chunk import QAChunk
from typing import Union


class RetrieveType(StrEnum):
    """Retrieval type enumeration"""
    PARTICIPLE = "participle"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    Graph = "graph"


class ChunkCreate(BaseModel):
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
    query: str
    kb_ids: list[uuid.UUID]
    ex_ids: list[str] | None = Field(None)
    file_names_filter: list[str] | None = Field(None)
    similarity_threshold: float | None = Field(None)
    vector_similarity_weight: float | None = Field(None)
    top_k: int | None = Field(100, ge=1, le=100)
    retrieve_type: RetrieveType | None = Field(None)
    rerank_score_threshold: float | None = Field(None, ge=0, le=1)


class ChunkBatchCreate(BaseModel):
    """批量创建 chunk"""
    items: list[ChunkCreate] = Field(..., min_length=1, description="chunk 列表")
