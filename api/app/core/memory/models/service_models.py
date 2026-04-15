from pydantic import BaseModel, Field, field_serializer, ConfigDict

from app.core.memory.enums import Neo4jNodeType, StorageType
from app.schemas.memory_config_schema import MemoryConfig


class MemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    end_user_id: str
    memory_config: MemoryConfig
    storage_type: StorageType = StorageType.NEO4J
    user_rag_memory_id: str | None = None
    language: str = "zh"


class Memory(BaseModel):
    source: Neo4jNodeType = Field(...)
    score: float = Field(default=0.0)
    content: str = Field(default="")
    data: dict = Field(default_factory=dict)
    query: str = Field(...)

    @field_serializer("source")
    def serialize_source(self, v) -> str:
        return v.value
