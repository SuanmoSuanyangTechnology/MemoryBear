from typing import Self

from pydantic import BaseModel, Field, field_serializer, ConfigDict, model_validator, computed_field

from app.core.memory.enums import Neo4jNodeType, StorageType
from app.core.validators import file_validator
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
    id: str = Field(...)

    @field_serializer("source")
    def serialize_source(self, v) -> str:
        return v.value


class MemorySearchResult(BaseModel):
    memories: list[Memory]
    content_str: str = Field(default="")

    @property
    def content(self) -> str:
        if self.content_str:
            return self.content_str
        return "\n".join([memory.content for memory in self.memories])

    @computed_field
    @property
    def count(self) -> int:
        return len(self.memories)

    def filter(self, score_threshold: float) -> Self:
        self.memories = [memory for memory in self.memories if memory.score >= score_threshold]
        return self

    def __add__(self, other: "MemorySearchResult") -> "MemorySearchResult":
        if not isinstance(other, MemorySearchResult):
            raise TypeError("")

        merged = MemorySearchResult(memories=list(self.memories))

        ids = {m.id for m in merged.memories}

        for memory in other.memories:
            if memory.id not in ids:
                merged.memories.append(memory)
                ids.add(memory.id)

        return merged


