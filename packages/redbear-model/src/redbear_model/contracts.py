"""Pure model registry and runtime contracts."""

from __future__ import annotations

import logging
import math
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    model_validator,
)

logger = logging.getLogger(__name__)


class ModelType(StrEnum):
    LLM = "llm"
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    IMAGE = "image"
    VIDEO = "video"


class ModelCapability(StrEnum):
    VISION = "vision"
    AUDIO = "audio"
    VIDEO = "video"
    THINKING = "thinking"
    THINKING_ONLY = "thinking_only"
    JSON_OUTPUT = "json_output"
    FUNCTION_CALL = "function_call"


class ModelProvider(StrEnum):
    OPENAI = "openai"
    SPEEDBEAR = "speedbear"
    DASHSCOPE = "dashscope"
    OLLAMA = "ollama"
    XINFERENCE = "xinference"
    GPUSTACK = "gpustack"
    BEDROCK = "bedrock"
    VOLCANO = "volcano"
    COMPOSITE = "composite"


class LoadBalanceStrategy(StrEnum):
    ROUND_ROBIN = "round_robin"
    NONE = "none"


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


QWEN3_VL_EMBEDDING_DIMENSION = 2048
type SupportedImageMediaType = Literal[
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
]


class EmbeddingPurpose(StrEnum):
    INDEX = "index"
    RETRIEVAL = "retrieval"


class TextEmbeddingContent(ContractModel):
    type: Literal["text"] = "text"
    text: str

    @model_validator(mode="after")
    def normalize_text(self) -> TextEmbeddingContent:
        text = self.text.strip()
        if not text:
            raise ValueError("embedding text must not be blank")
        object.__setattr__(self, "text", text)
        return self


class ImageEmbeddingContent(ContractModel):
    type: Literal["image"] = "image"
    media_type: SupportedImageMediaType
    data_uri: str = Field(min_length=1, repr=False)
    decoded_bytes: int = Field(ge=1, repr=False)


type EmbeddingContent = TextEmbeddingContent | ImageEmbeddingContent


class EmbeddingRequest(ContractModel):
    purpose: EmbeddingPurpose
    contents: tuple[EmbeddingContent, ...] = Field(min_length=1, max_length=20)
    dimension: Literal[2048] = QWEN3_VL_EMBEDDING_DIMENSION
    fusion: Literal[True] = True

    @model_validator(mode="after")
    def validate_image_count(self) -> EmbeddingRequest:
        if sum(isinstance(item, ImageEmbeddingContent) for item in self.contents) > 10:
            raise ValueError("embedding request supports at most 10 images")
        return self


class EmbeddingResult(ContractModel):
    vector: tuple[float, ...] = Field(min_length=1, repr=False)
    dimension: Literal[2048] = QWEN3_VL_EMBEDDING_DIMENSION
    usage: dict[str, int] = Field(default_factory=dict)


type RerankQuery = TextEmbeddingContent | ImageEmbeddingContent


class RerankCandidateView(ContractModel):
    chunk_index: int = Field(ge=0)
    kind: Literal["text", "image"]
    content: str = Field(min_length=1, repr=False)
    image_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_image_index(self) -> RerankCandidateView:
        if self.kind == "image" and self.image_index is None:
            object.__setattr__(self, "image_index", 0)
        if self.kind == "text" and self.image_index is not None:
            raise ValueError("text rerank views cannot have an image index")
        return self


class RerankScore(ContractModel):
    input_index: int = Field(ge=0)
    relevance_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_finite_score(self) -> RerankScore:
        if not math.isfinite(self.relevance_score):
            raise ValueError("rerank score must be finite")
        return self


class ModelRuntimeOptions(ContractModel):
    timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    concurrency: int = Field(default=5, ge=1)
    http_max_connections: int = Field(default=300, ge=1)
    http_max_keepalive_connections: int = Field(default=50, ge=0)
    http_trust_env: bool = True
    bedrock_max_pool_connections: int = Field(default=50, ge=1)
    bedrock_max_retries: int = Field(default=2, ge=0)
    embedding_batch_size: int = Field(default=10, ge=1)


class ModelConfigSnapshot(ContractModel):
    model_config_id: UUID
    tenant_id: UUID
    provider: ModelProvider
    model_type: ModelType
    display_name: str = Field(min_length=1)
    is_active: bool
    is_public: bool
    load_balance_strategy: LoadBalanceStrategy = LoadBalanceStrategy.NONE
    capabilities: tuple[ModelCapability, ...] = ()
    is_omni: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict)


class ModelKeySnapshot(ContractModel):
    key_id: UUID
    model_name: str = Field(min_length=1)
    provider: ModelProvider
    api_key: SecretStr
    base_url: str | None = None
    is_active: bool
    priority: str = "1"
    usage_count: int = Field(default=0, ge=0)
    last_used_at_ms: int | None = Field(default=None, ge=0)
    capabilities: tuple[ModelCapability, ...] = ()
    is_omni: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict)


class PublicModelBindingSnapshot(ContractModel):
    tenant_id: UUID
    provider: ModelProvider
    api_key: SecretStr
    base_url: str | None = None


class ResolvedModelConfig(ContractModel):
    model_config_id: UUID
    key_id: UUID | None
    tenant_id: UUID
    provider: ModelProvider
    model_type: ModelType
    model_name: str = Field(min_length=1)
    api_key: SecretStr
    base_url: str | None = None
    capabilities: tuple[ModelCapability, ...] = ()
    is_omni: bool = False
    deep_thinking: bool = False
    thinking_budget_tokens: int | None = Field(default=None, ge=1)
    json_output: bool = False
    provider_params: dict[str, JsonValue] = Field(default_factory=dict)
    runtime: ModelRuntimeOptions = Field(default_factory=ModelRuntimeOptions)

    @model_validator(mode="after")
    def normalize_capability_flags(self) -> ResolvedModelConfig:
        deep_thinking, thinking_budget_tokens, json_output = normalize_runtime_flags(
            self.capabilities,
            self.deep_thinking,
            self.thinking_budget_tokens,
            self.json_output,
            self.model_name,
        )
        object.__setattr__(self, "deep_thinking", deep_thinking)
        object.__setattr__(self, "thinking_budget_tokens", thinking_budget_tokens)
        object.__setattr__(self, "json_output", json_output)
        return self


def normalize_runtime_flags(
    capabilities: tuple[ModelCapability, ...],
    deep_thinking: bool,
    thinking_budget_tokens: int | None,
    json_output: bool,
    model_name: str,
) -> tuple[bool, int | None, bool]:
    """Preserve the legacy RedBearModelConfig capability normalization."""
    has_thinking = ModelCapability.THINKING in capabilities
    has_thinking_only = ModelCapability.THINKING_ONLY in capabilities
    supports_json_output = ModelCapability.JSON_OUTPUT in capabilities

    if deep_thinking and not has_thinking and not has_thinking_only:
        logger.warning(
            "Model %s does not support thinking; disabling deep_thinking",
            model_name,
        )
        deep_thinking = False
        thinking_budget_tokens = None

    if not deep_thinking and thinking_budget_tokens is not None:
        logger.warning(
            "Thinking is disabled for model %s; clearing thinking_budget_tokens",
            model_name,
        )
        thinking_budget_tokens = None

    if has_thinking_only:
        deep_thinking = True
        thinking_budget_tokens = None
        if json_output:
            logger.warning(
                "thinking_only model %s does not support JSON output",
                model_name,
            )
            json_output = False

    if json_output and not supports_json_output:
        logger.warning(
            "Model %s capability does not include json_output; disabling it",
            model_name,
        )
        json_output = False

    return deep_thinking, thinking_budget_tokens, json_output
