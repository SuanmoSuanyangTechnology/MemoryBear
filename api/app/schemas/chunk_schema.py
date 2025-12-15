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


class ChunkCreate(BaseModel):
    content: Union[str, QAChunk] = Field(
        description="Content can be either a string or a QAChunk object"
    )

    @property
    def chunk_content(self) -> str:
        """
        Get the actual content string regardless of input type
        """
        if isinstance(self.content, QAChunk):
            return f"question: {self.content.question} answer: {self.content.answer}"
        return self.content


class ChunkUpdate(BaseModel):
    content: Union[str, QAChunk] = Field(
        description="Content can be either a string or a QAChunk object"
    )

    @property
    def chunk_content(self) -> str:
        """
        Get the actual content string regardless of input type
        """
        if isinstance(self.content, QAChunk):
            return f"question: {self.content.question} answer: {self.content.answer}"
        return self.content


class ChunkRetrieve(BaseModel):
    query: str
    kb_ids: list[uuid.UUID]
    similarity_threshold: float | None = Field(None)
    vector_similarity_weight: float | None = Field(None)
    top_k: int | None = Field(None)
    retrieve_type: RetrieveType | None = Field(None)
