from typing import Self

from pydantic import BaseModel, Field, field_serializer, ConfigDict, computed_field

from app.core.memory.enums import Neo4jNodeType, StorageType
from app.schemas.memory_config_schema import MemoryConfig


class MemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    end_user_id: str
    memory_config: MemoryConfig | None = None
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


class RelationMemory(BaseModel):
    source: str = ""
    relation: str = ""
    target: str = ""
    target_desc: str = ""
    target_id: str = ""

    @property
    def dedup_key(self) -> tuple:
        return self.source, self.relation, self.target


class EntityPair(BaseModel):
    source_id: str = ""
    target_id: str = ""


class RelationSearchResult(BaseModel):
    pairs: list[EntityPair] = Field(default_factory=list)


class MemorySearchResult(BaseModel):
    memories: list[Memory]
    relations: list[RelationMemory] = Field(default_factory=list)
    content_str: str = Field(default="")

    @property
    def content(self) -> str:
        parts = []
        if self.content_str:
            parts.append(self.content_str)
        else:
            parts.append("\n".join([memory.content for memory in self.memories]))
        if self.relations:
            from app.core.memory.read_services.search_engine.result_builder import build_relation_content
            parts.append("\n".join([build_relation_content(rel) for rel in self.relations]))
        return "\n".join(parts)

    @computed_field
    @property
    def count(self) -> int:
        return len(self.memories)

    def filter(self, score_threshold: float) -> Self:
        self.memories = [memory for memory in self.memories if memory.score >= score_threshold]
        return self

    def _dedup_relations(self) -> None:
        seen = set()
        unique = []
        for rel in self.relations:
            key = rel.dedup_key
            if key not in seen:
                seen.add(key)
                unique.append(rel)
        self.relations = unique

    def __add__(self, other: "MemorySearchResult") -> "MemorySearchResult":
        if not isinstance(other, MemorySearchResult):
            raise TypeError("")

        merged = MemorySearchResult(memories=list(self.memories))

        ids = {m.id for m in merged.memories}

        for memory in other.memories:
            if memory.id not in ids:
                merged.memories.append(memory)
                ids.add(memory.id)

        merged.relations = list(self.relations)
        seen_rel_keys = {r.dedup_key for r in merged.relations}
        for rel in other.relations:
            key = rel.dedup_key
            if key not in seen_rel_keys:
                merged.relations.append(rel)
                seen_rel_keys.add(key)

        return merged
