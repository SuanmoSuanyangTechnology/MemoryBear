"""Lightweight public exports for RedBear model resolution."""

from .contracts import (
    LoadBalanceStrategy,
    ModelCapability,
    ModelConfigSnapshot,
    ModelKeySnapshot,
    ModelProvider,
    ModelRuntimeOptions,
    ModelType,
    PublicModelBindingSnapshot,
    ResolvedModelConfig,
)
from .errors import (
    ModelAccessDeniedError,
    ModelConfigInactiveError,
    ModelConfigNotFoundError,
    ModelCredentialNotFoundError,
    ModelUsageRecordError,
    ProviderDependencyMissingError,
    PublicCredentialUnavailableError,
    RedBearModelError,
    UnsupportedModelProviderError,
)
from .ports import AsyncModelRegistryRepository, ModelRegistryRepository
from .resolver import (
    record_model_usage,
    record_model_usage_async,
    resolve_model,
    resolve_model_async,
)

__all__ = [
    "AsyncModelRegistryRepository",
    "LoadBalanceStrategy",
    "ModelAccessDeniedError",
    "ModelCapability",
    "ModelConfigInactiveError",
    "ModelConfigNotFoundError",
    "ModelConfigSnapshot",
    "ModelCredentialNotFoundError",
    "ModelKeySnapshot",
    "ModelProvider",
    "ModelRegistryRepository",
    "ModelRuntimeOptions",
    "ModelType",
    "ModelUsageRecordError",
    "ProviderDependencyMissingError",
    "PublicCredentialUnavailableError",
    "PublicModelBindingSnapshot",
    "RedBearModelError",
    "ResolvedModelConfig",
    "UnsupportedModelProviderError",
    "record_model_usage",
    "record_model_usage_async",
    "resolve_model",
    "resolve_model_async",
]
