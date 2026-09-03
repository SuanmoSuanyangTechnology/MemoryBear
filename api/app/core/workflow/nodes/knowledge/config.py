from typing import Any
from uuid import UUID

from pydantic import Field, BaseModel, field_validator, model_validator

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.llm.config import (
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

from app.schemas.chunk_schema import KnowledgeBaseConfig
from app.schemas.knowledge_metadata_schema import FilterGroup, MetadataFilterMode
from app.schemas.rerank_schema import RerankMode, RerankWeights


class KnowledgeModelCompletionParamsConfig(BaseModel):
    """知识检索节点 auto 模式 LLM 生成参数（与 agent 节点 completion_params 结构一致）。"""

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
    """知识检索节点 auto 模式模型配置。

    结构与 agent 节点的 AgentModelConfig 一致：model_id + 嵌套 completion_params。
    另含一个 model_validator，兼容前端 ModelConfig 发出的扁平结构（参数平铺在
    model_id 同级），自动收进 completion_params，避免参数静默丢失。
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
        """兼容前端 ModelConfig 发出的扁平结构。

        前端（ModelConfigForm）把模型参数平铺在 model_id 同级（temperature/top_p/...），
        而本模型期望它们嵌套在 completion_params 下。若检测到扁平的参数字段且未显式
        提供 completion_params，则自动把它们收进 completion_params。
        model_id / provider / model / model_type 保留在顶层。
        """
        if not isinstance(value, dict):
            return value
        # 已显式提供 completion_params 时，优先尊重嵌套结构（向后兼容）
        if value.get("completion_params") is not None:
            return value

        flat_param_keys = {
            "temperature", "max_tokens", "top_p", "top_k", "seed",
            "repetition_penalty", "frequency_penalty", "presence_penalty",
            "search", "thinking", "response_format", "extra_headers",
            "stop", "json_output", "structured_output",
            "enable_search",  # 前端历史字段名兼容
        }
        collected = {k: v for k, v in value.items() if k in flat_param_keys and v is not None}
        if not collected:
            return value
        normalized = {k: v for k, v in value.items() if k not in flat_param_keys}
        normalized["completion_params"] = collected
        return normalized


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
