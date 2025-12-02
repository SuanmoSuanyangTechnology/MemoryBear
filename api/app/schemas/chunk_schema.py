from pydantic import BaseModel, Field
import uuid
from enum import StrEnum


class RetrieveType(StrEnum):
    """Retrieval type enumeration"""
    PARTICIPLE = "participle"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"

class ChunkCreate(BaseModel):
    content: str


class ChunkUpdate(BaseModel):
    content: str | None = Field(None)


class ChunkRetrieve(BaseModel):
    query: str
    kb_ids: list[uuid.UUID]
    similarity_threshold: float | None = Field(None)
    vector_similarity_weight: float | None = Field(None)
    top_k: int | None = Field(None)
    retrieve_type: RetrieveType | None = Field(None)