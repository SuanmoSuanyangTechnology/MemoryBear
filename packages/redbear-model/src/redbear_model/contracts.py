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
    name: str = Field(min_length=1)  # 解析锚点名：非组合 config 的 name 即真实调用名（组合模型调用名在 members 声明）
    is_active: bool
    is_public: bool
    load_balance_strategy: LoadBalanceStrategy = LoadBalanceStrategy.NONE
    capabilities: tuple[ModelCapability, ...] = ()
    is_omni: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict)


# deprecated（阶段一过渡）：mem-knowledge 旧读窗口消费；M3 切 model_channels 后由 ChannelSnapshot 取代，收尾任务删除
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
    key_id: UUID | None  # v1 遗留：旧 usage 回写用，M3 渠道化后废弃
    channel_id: UUID | None = None  # v2：命中渠道 id（usage 事件 / 编排追踪）
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


class ChannelSource:
    """渠道来源（中性措辞，不带企业概念）：租户自管 / 平台代管。"""
    MANUAL = "manual"
    PLATFORM = "platform"


class ChannelSnapshot(ContractModel):
    """渠道登记快照（registry/resolver 用；密文信封原样传递，解密收敛在 resolver 取凭据处）。

    model_names == ()  = provider 级默认渠道，覆盖该 provider 全部未点名模型；
    非空 = 点名渠道，仅匹配列出的模型名。点名态只看 model_names，与 api_base 无关。
    """
    id: UUID
    tenant_id: UUID
    provider: str
    model_names: tuple[str, ...] = ()
    api_base: str | None = None          # 执行端点属性，不参与覆盖匹配
    credential_encrypted: str            # 信封 v{ver}:iv:tag:ct
    credential_sha256: str
    credential_masked: str
    is_active: bool = True
    priority: int = 0
    cooldown_until_ms: int | None = None  # 熔断预留（阶段一恒空）
    source: str = ChannelSource.MANUAL
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    created_at_ms: int
    updated_at_ms: int

    @property
    def is_provider_level(self) -> bool:
        return not self.model_names

    def covers(self, model_name: str) -> bool:
        return self.is_provider_level or model_name in self.model_names


class CompositeMember(BaseModel):
    """组合模型成员声明（config JSON members[] 的序列化形状，spec §10.3）。"""
    provider: str
    model_name: str
