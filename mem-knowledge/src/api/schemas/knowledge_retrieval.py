"""Prepared retrieval contracts copied from the legacy API."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ...rag.models.chunk import DocumentChunk
from .chunk import (
    ImageRetrievalQuery,
    KnowledgeBaseConfig,
    KnowledgeRetrievalSource,
    RetrievalQuery,
    RetrieveType,
    TextRetrievalQuery,
    normalize_retrieval_query,
)
from .knowledge_metadata import FilterGroup, MetadataFilterMode
from .rerank import RerankMode, RerankWeights


class KnowledgeRetrievalRequest(BaseModel):
    query: RetrievalQuery
    kb_ids: list[UUID] = Field(default_factory=list)
    ex_ids: list[str] = Field(default_factory=list)
    knowledge_bases: list[KnowledgeBaseConfig] = Field(default_factory=list)
    file_names_filter: list[str] = Field(default_factory=list)
    similarity_threshold: float = Field(default=0.3, ge=0, le=1)
    vector_similarity_weight: float | None = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=100, ge=1, le=100)
    top_n: int | None = Field(default=None, ge=1, le=100)
    source: KnowledgeRetrievalSource = KnowledgeRetrievalSource.GENERAL
    retrieve_type: RetrieveType = RetrieveType.HYBRID
    enable_graph_retrieval: int = Field(default=0, ge=0, le=1)
    rerank_id: UUID | None = None
    rerank_score_threshold: float | None = Field(default=None, ge=0, le=1)
    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None
    metadata_filters: list[FilterGroup] = Field(default_factory=list)
    metadata_filter_mode: MetadataFilterMode = MetadataFilterMode.MANUAL
    metadata_filters_resolved: bool = False

    def mark_metadata_filters_resolved(self) -> None:
        self.metadata_filters_resolved = True

    @property
    def graph_retrieval_mix_enabled(self) -> bool:
        return self.enable_graph_retrieval == 1

    def graph_retrieval_mix_enabled_for(
        self,
        config: KnowledgeBaseConfig | None,
    ) -> bool:
        if (
            config is not None
            and "enable_graph_retrieval" in config.model_fields_set
            and config.enable_graph_retrieval is not None
        ):
            return config.enable_graph_retrieval == 1
        return self.graph_retrieval_mix_enabled

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: RetrievalQuery) -> RetrievalQuery:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("query must not be empty")
            return stripped
        return value

    @property
    def normalized_query(self) -> TextRetrievalQuery | ImageRetrievalQuery:
        return normalize_retrieval_query(self.query)

    @property
    def query_text(self) -> str | None:
        query = self.normalized_query
        return query.content if isinstance(query, TextRetrievalQuery) else None

    @model_validator(mode="after")
    def validate_knowledge_ids(self) -> "KnowledgeRetrievalRequest":
        if not self.kb_ids and not self.ex_ids and not self.knowledge_bases:
            raise ValueError("kb_ids, ex_ids and knowledge_bases cannot all be empty")
        if self.top_n is None or "top_n" not in self.model_fields_set:
            self.top_n = max(self.top_k, 20)
        elif self.top_n < self.top_k:
            raise ValueError("top_n must be greater than or equal to top_k")
        if self.rerank_score_threshold is None:
            self.rerank_score_threshold = self.vector_similarity_weight
        return self


class KnowledgeRetrievalResult(BaseModel):
    chunks: list[DocumentChunk] = Field(default_factory=list)
    entities: list[Any] = Field(default_factory=list)
    relationships: list[Any] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def has_graph_data(self) -> bool:
        return bool(self.entities or self.relationships)
