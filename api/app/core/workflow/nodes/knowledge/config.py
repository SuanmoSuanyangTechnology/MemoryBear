from uuid import UUID

from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.schemas.chunk_schema import RetrieveType


class KnowledgeRetrievalNodeConfig(BaseNodeConfig):
    query: str = Field(
        ...,
        description="Search query string"
    )

    kb_ids: list[UUID] = Field(
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

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "query": "{{sys.message}}",
                    "kb_ids": [
                        "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                    ],
                    "similarity_threshold": 0.2,
                    "vector_similarity_weight": 0.3,
                    "top_k": 1,
                    "retrieve_type": "hybrid"
                }
            ]
        }
