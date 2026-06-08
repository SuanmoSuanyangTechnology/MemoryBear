from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import FilterGroup, MetadataFilterMode


class KnowledgeRetrievalRequest(BaseModel):
    query: str
    kb_ids: list[UUID] = Field(default_factory=list)
    ex_ids: list[str] = Field(default_factory=list)
    similarity_threshold: float = Field(default=0.3, ge=0, le=1)
    vector_similarity_weight: float = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=100, ge=1, le=100)
    retrieve_type: RetrieveType = RetrieveType.HYBRID
    rerank_id: UUID | None = None
    rerank_score_threshold: float | None = Field(default=None, ge=0, le=1)
    metadata_filters: list[FilterGroup] = Field(default_factory=list)
    metadata_filter_mode: MetadataFilterMode = MetadataFilterMode.MANUAL

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be empty")
        return stripped

    @model_validator(mode="after")
    def validate_knowledge_ids(self) -> "KnowledgeRetrievalRequest":
        if not self.kb_ids and not self.ex_ids:
            raise ValueError("kb_ids and ex_ids cannot both be empty")
        if self.rerank_score_threshold is None:
            self.rerank_score_threshold = self.vector_similarity_weight
        return self


class KnowledgeRetrievalResult(BaseModel):
    chunks: list[Any] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)
