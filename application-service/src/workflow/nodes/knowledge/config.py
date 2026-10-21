from typing import Any
from uuid import UUID

from pydantic import Field, BaseModel, field_validator, model_validator

from ...nodes.base_config import BaseNodeConfig
from ...nodes.llm.config import (
    LLMExtraHeadersConfig,
    LLMFrequencyPenaltyConfig,
    LLMPresencePenaltyConfig,
    LLMRepetitionPenaltyConfig,
    LLMResponseFormatConfig,
    LLMSeedConfig,
    LLMStopConfig,
    LLMThinkingConfig,
    LLMTopKConfig,
    LLMTopPConfig,
)

from ....schemas.chunk_schema import KnowledgeBaseConfig
from ....schemas.knowledge_metadata_schema import FilterGroup, MetadataFilterMode
from ....schemas.rerank_schema import RerankMode, RerankWeights


class KnowledgeModelCompletionParamsConfig(BaseModel):
    """LLM generation params for the auto mode (mirrors the agent node completion_params)."""

    temperature: float | None = Field(default=0.7, ge=0.0, le=2.0, description="Temperature")
    max_tokens: int | None = Field(default=2000, ge=1, le=32000, description="Max output tokens")
    top_p: LLMTopPConfig = Field(default_factory=LLMTopPConfig, description="Top-p sampling config")
    top_k: LLMTopKConfig = Field(default_factory=LLMTopKConfig, description="Top-k sampling config")
    seed: LLMSeedConfig = Field(default_factory=LLMSeedConfig, description="Random seed config")
    repetition_penalty: LLMRepetitionPenaltyConfig = Field(default_factory=LLMRepetitionPenaltyConfig, description="Repetition penalty config")
    frequency_penalty: LLMFrequencyPenaltyConfig = Field(default_factory=LLMFrequencyPenaltyConfig, description="Frequency penalty config")
    presence_penalty: LLMPresencePenaltyConfig = Field(default_factory=LLMPresencePenaltyConfig, description="Presence penalty config")
    search: bool = Field(default=False, description="Enable model search")
    thinking: LLMThinkingConfig = Field(default_factory=LLMThinkingConfig, description="Thinking config")
    response_format: LLMResponseFormatConfig = Field(default_factory=LLMResponseFormatConfig, description="Response format config")
    extra_headers: LLMExtraHeadersConfig = Field(default_factory=LLMExtraHeadersConfig, description="Extra request headers config")
    stop: LLMStopConfig = Field(default_factory=LLMStopConfig, description="Stop sequence config")
    json_output: bool = Field(default=False, description="Force JSON output")
    structured_output: bool = Field(
        default=False,
        description="Whether to expose parsed JSON as structured_output and request JSON Schema output",
    )

    @field_validator("response_format", mode="before")
    @classmethod
    def coerce_response_format(cls, v):
        if isinstance(v, str):
            return LLMResponseFormatConfig(enable=True, value=v)
        return v


class KnowledgeModelConfig(BaseModel):
    """Model config for the knowledge retrieval node's auto mode.

    Same structure as the agent node's AgentModelConfig: model_id plus nested
    completion_params. Also carries a model_validator that accepts the flat structure
    emitted by the frontend ModelConfig (parameters at the same level as model_id) and
    automatically collects them into completion_params so parameters are not silently lost.
    """

    model_id: UUID | None = Field(default=None, description="Model config ID")
    provider: str | None = Field(default=None, description="Model provider")
    model: str | None = Field(default=None, description="Provider model name")
    model_type: str | None = Field(default="llm", description="Model type")
    completion_params: KnowledgeModelCompletionParamsConfig = Field(
        default_factory=KnowledgeModelCompletionParamsConfig,
        description="Model completion parameters",
    )

    @model_validator(mode="before")
    @classmethod
    def _flatten_completion_params(cls, value: Any) -> Any:
        """Accept the flat structure emitted by the frontend ModelConfig.

        The frontend (ModelConfigForm) places model parameters at the same level as
        model_id (temperature/top_p/...), while this model expects them nested under
        completion_params. When flat parameter fields are detected and no explicit
        completion_params is provided, they are automatically collected into
        completion_params. model_id / provider / model / model_type stay at the top level.
        """
        if not isinstance(value, dict):
            return value
        # Explicit completion_params wins; the nested structure is kept (backward compatible)
        if value.get("completion_params") is not None:
            return value

        flat_param_keys = {
            "temperature", "max_tokens", "top_p", "top_k", "seed",
            "repetition_penalty", "frequency_penalty", "presence_penalty",
            "search", "thinking", "response_format", "extra_headers",
            "stop", "json_output", "structured_output",
            "enable_search",  # legacy frontend field-name alias
        }
        collected = {k: v for k, v in value.items() if k in flat_param_keys and v is not None}
        if not collected:
            return value
        normalized = {k: v for k, v in value.items() if k not in flat_param_keys}
        normalized["completion_params"] = collected
        return normalized


class KnowledgeRetrievalNodeConfig(BaseNodeConfig):
    query: str = Field(
        default="",
        description="文本检索 query（字符串或字符串变量模板），与 image_query 二选一"
    )

    image_query: str | None = Field(
        default=None,
        description="图片检索变量引用，仅支持纯变量引用 file / array[file]（数组取第一张图），与 query 二选一"
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

    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None

    metadata_filter_mode: MetadataFilterMode = Field(
        default=MetadataFilterMode.DISABLED,
        description="Node-level metadata filter mode (disabled / manual / auto), "
                    "applies to all knowledge bases in this node"
    )

    metadata_filters: FilterGroup | None = Field(
        default=None,
        description="Single filter condition group used in manual mode; ignored in auto/disabled mode"
    )

    metadata_model: KnowledgeModelConfig = Field(
        default_factory=KnowledgeModelConfig,
        description="auto 模式专用模型与参数（仅当 metadata_filter_mode=auto 时使用，model_id 必填）"
    )

    @model_validator(mode="after")
    def _validate_query_exclusive(self) -> "KnowledgeRetrievalNodeConfig":
        """Text query and image_query are mutually exclusive and cannot both be empty."""
        has_text = bool((self.query or "").strip())
        has_image = bool((self.image_query or "").strip())
        if has_text and has_image:
            raise ValueError("query 与 image_query 只能二选一，不能同时填写")
        if not has_text and not has_image:
            raise ValueError("query 与 image_query 必须填写其中一个")
        return self

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "query": "{{sys.message}}",
                    "image_query": None,
                    "knowledge_bases": [{
                        "kb_id": "xxxxxxxx-xxxx-xxxx-xxxxxxxxxxxxxxxxx",
                        "similarity_threshold": 0.2,
                        "vector_similarity_weight": 0.3,
                        "top_k": 4,
                        "retrieve_type": "hybrid",
                        "enable_graph_retrieval": 1
                    }],
                    "metadata_filter_mode": "disabled",
                    "metadata_filters": None,
                    "metadata_model": {
                        "model_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                        "completion_params": {"temperature": 0}
                    },
                    "reranker_top_k": 1,
                    "reranker_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                }
            ]
        }
