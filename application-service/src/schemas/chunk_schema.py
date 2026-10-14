"""Knowledge-retrieval node config contracts (frozen subset of the host
``app/schemas/chunk_schema.py``).

Only the classes the workflow definition-plane closure consumes are ported
(``RetrieveType`` and ``KnowledgeBaseConfig``); field names, defaults and
constraints must stay byte-compatible with the host so imported workflow
nodes validate identically on both sides.
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

from .rerank_schema import RerankMode, RerankWeights


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


__all__ = ["KnowledgeBaseConfig", "RetrieveType"]
