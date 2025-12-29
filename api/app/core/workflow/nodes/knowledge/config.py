from uuid import UUID

from pydantic import Field, BaseModel

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.schemas.chunk_schema import RetrieveType


class KnowledgeBaseConfig(BaseModel):
    kb_id: UUID = Field(
        ...,
        description="Knowledge base IDs"
    )

    similarity_threshold: float = Field(
        default=0.2,
        description="Knowledge base similarity threshold"
    )

    vector_similarity_weight: float = Field(
        default=0.3,
        description="Knowledge base vector similarity weight"
    )

    top_k: int = Field(
        default=4,
        description="Knowledge base top k"
    )

    retrieve_type: RetrieveType = Field(
        default=RetrieveType.PARTICIPLE,
        description="Retrieve type"
    )


class KnowledgeRetrievalNodeConfig(BaseNodeConfig):
    query: str = Field(
        ...,
        description="Search query string"
    )

    knowledge_bases: list[KnowledgeBaseConfig] = Field(
        ...,
        description="Knowledge base config"
    )

    reranker_id: UUID = Field(
        ...,
        description="Reranker top k"
    )

    reranker_top_k: int = Field(
        default=4,
        description="Knowledge base top k"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "query": "{{sys.message}}",
                    "knowledge_bases": [{
                        "kb_id": "xxxxxxxx-xxxx-xxxx-xxxxxxxxxxxxxxxxx",
                        "similarity_threshold": 0.2,
                        "vector_similarity_weight": 0.3,
                        "top_k": 4,
                        "retrieve_type": "hybrid"
                    }],
                    "reranker_top_k": 1,
                    "reranker_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                }
            ]
        }
