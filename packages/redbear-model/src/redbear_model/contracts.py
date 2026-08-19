"""Pure model registry and runtime contracts."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr


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
    model_name: str = Field(min_length=1)
    api_key: SecretStr
    base_url: str | None = None
    capabilities: tuple[ModelCapability, ...] = ()
    is_omni: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict)


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
