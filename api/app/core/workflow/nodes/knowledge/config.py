from uuid import UUID

from pydantic import Field, BaseModel

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.llm.config import (
    LLMTopPConfig,
    LLMTopKConfig,
    LLMSeedConfig,
    LLMRepetitionPenaltyConfig,
    LLMFrequencyPenaltyConfig,
    LLMPresencePenaltyConfig,
    LLMThinkingConfig,
    LLMResponseFormatConfig,
    LLMExtraHeadersConfig,
    LLMStopConfig,
)
from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import FilterGroup, MetadataFilterMode


class MetadataAutoModelCompletionParams(BaseModel):
    """Auto mode LLM completion parameters — mirrors AgentModelCompletionParamsConfig"""
    temperature: float | None = Field(default=0, ge=0.0, le=2.0, description="Temperature")
    max_tokens: int | None = Field(default=2000, ge=1, le=32000, description="Max output tokens")
    top_p: LLMTopPConfig = Field(default_factory=LLMTopPConfig)
    top_k: LLMTopKConfig = Field(default_factory=LLMTopKConfig)
    seed: LLMSeedConfig = Field(default_factory=LLMSeedConfig)
    repetition_penalty: LLMRepetitionPenaltyConfig = Field(default_factory=LLMRepetitionPenaltyConfig)
    frequency_penalty: LLMFrequencyPenaltyConfig = Field(default_factory=LLMFrequencyPenaltyConfig)
    presence_penalty: LLMPresencePenaltyConfig = Field(default_factory=LLMPresencePenaltyConfig)
    search: bool = Field(default=False, description="Enable model search")
    thinking: LLMThinkingConfig = Field(default_factory=LLMThinkingConfig)
    response_format: LLMResponseFormatConfig = Field(default_factory=LLMResponseFormatConfig)
    extra_headers: LLMExtraHeadersConfig = Field(default_factory=LLMExtraHeadersConfig)
    stop: LLMStopConfig = Field(default_factory=LLMStopConfig)
    json_output: bool = Field(default=True, description="Force JSON output for structured extraction")
    structured_output: bool = Field(
        default=False,
        description="Whether to expose parsed JSON as structured_output and request JSON Schema output",
    )


class MetadataAutoModelConfig(BaseModel):
    """Auto mode LLM model config — mirrors AgentModelConfig but for metadata extraction"""
    model_id: UUID | None = Field(default=None, description="Model config ID")
    provider: str | None = Field(default=None, description="Model provider")
    model: str | None = Field(default=None, description="Provider model name")
    model_type: str | None = Field(default="chat", description="Model type")
    completion_params: MetadataAutoModelCompletionParams = Field(
        default_factory=MetadataAutoModelCompletionParams,
        description="Model completion parameters"
    )


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
        default_factory=list,
        description="Knowledge base config"
    )

    reranker_id: UUID | None = Field(
        default=None,
        description="Reranker top k"
    )

    reranker_top_k: int = Field(
        default=4,
        description="Knowledge base top k"
    )

    metadata_filter_mode: MetadataFilterMode = Field(
        default=MetadataFilterMode.DISABLED,
        description="Node-level metadata filter mode (disabled / manual / auto), "
                    "applies to all knowledge bases in this node"
    )

    metadata_filters: FilterGroup | None = Field(
        default=None,
        description="Single filter condition group used in manual mode; ignored in auto/disabled mode"
    )

    metadata_model: MetadataAutoModelConfig | None = Field(
        default=None,
        description="LLM model config used in auto mode; fallback to knowledge base's llm_id if not set"
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
                    "metadata_filter_mode": "disabled",
                    "metadata_filters": None,
                    "metadata_model": None,
                    "reranker_top_k": 1,
                    "reranker_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                }
            ]
        }
