"""Pure model visibility, credential selection, and resolution rules."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import SecretStr

from .contracts import (
    LoadBalanceStrategy,
    ModelCapability,
    ModelConfigSnapshot,
    ModelKeySnapshot,
    ModelProvider,
    ModelRuntimeOptions,
    PublicModelBindingSnapshot,
    ResolvedModelConfig,
)
from .errors import (
    ModelAccessDeniedError,
    ModelConfigInactiveError,
    ModelConfigNotFoundError,
    ModelCredentialNotFoundError,
    ModelUsageRecordError,
    PublicCredentialUnavailableError,
)
from .ports import AsyncModelRegistryRepository, ModelRegistryRepository


def _validate_config_access(
    config: ModelConfigSnapshot,
    tenant_id: UUID,
) -> None:
    if not config.is_active:
        raise ModelConfigInactiveError(config.model_config_id)
    if config.tenant_id != tenant_id and not config.is_public:
        raise ModelAccessDeniedError(config.model_config_id, tenant_id)


def _select_key(
    config: ModelConfigSnapshot,
    keys: Sequence[ModelKeySnapshot],
) -> ModelKeySnapshot:
    active_keys = [key for key in keys if key.is_active]
    if not active_keys:
        raise ModelCredentialNotFoundError(config.model_config_id)
    if config.load_balance_strategy is LoadBalanceStrategy.ROUND_ROBIN:
        return min(
            active_keys,
            key=lambda key: (
                key.usage_count,
                key.last_used_at_ms if key.last_used_at_ms is not None else -1,
            ),
        )
    return active_keys[0]


def _runtime_flags(
    params: dict,
) -> tuple[bool, int | None, bool]:
    deep_thinking = bool(params.get("deep_thinking", False))
    raw_budget = params.get("thinking_budget_tokens")
    thinking_budget = int(raw_budget) if raw_budget is not None else None
    json_output = bool(params.get("json_output", False))
    return deep_thinking, thinking_budget, json_output


def _build_resolved(
    config: ModelConfigSnapshot,
    *,
    key_id: UUID | None,
    tenant_id: UUID,
    provider: ModelProvider,
    model_name: str,
    api_key: SecretStr,
    base_url: str | None,
    capabilities: tuple[ModelCapability, ...],
    is_omni: bool,
    params: dict,
    runtime_options: ModelRuntimeOptions | None,
) -> ResolvedModelConfig:
    deep_thinking, thinking_budget, json_output = _runtime_flags(
        params,
    )
    return ResolvedModelConfig(
        model_config_id=config.model_config_id,
        key_id=key_id,
        tenant_id=tenant_id,
        provider=provider,
        model_type=config.model_type,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        capabilities=capabilities,
        is_omni=is_omni,
        deep_thinking=deep_thinking,
        thinking_budget_tokens=thinking_budget,
        json_output=json_output,
        provider_params=params,
        runtime=runtime_options or ModelRuntimeOptions(),
    )


def _build_from_key(
    config: ModelConfigSnapshot,
    key: ModelKeySnapshot,
    tenant_id: UUID,
    runtime_options: ModelRuntimeOptions | None,
) -> ResolvedModelConfig:
    return _build_resolved(
        config,
        key_id=key.key_id,
        tenant_id=tenant_id,
        provider=key.provider,
        model_name=key.model_name,
        api_key=key.api_key,
        base_url=key.base_url,
        capabilities=key.capabilities or config.capabilities,
        is_omni=key.is_omni or config.is_omni,
        params=dict(key.config or config.config),
        runtime_options=runtime_options,
    )


def _build_from_binding(
    config: ModelConfigSnapshot,
    binding: PublicModelBindingSnapshot,
    tenant_id: UUID,
    runtime_options: ModelRuntimeOptions | None,
) -> ResolvedModelConfig:
    return _build_resolved(
        config,
        key_id=None,
        tenant_id=tenant_id,
        provider=binding.provider,
        model_name=binding.model_name,
        api_key=binding.api_key,
        base_url=binding.base_url,
        capabilities=binding.capabilities or config.capabilities,
        is_omni=binding.is_omni or config.is_omni,
        params=dict(binding.config or config.config),
        runtime_options=runtime_options,
    )


def resolve_model(
    repository: ModelRegistryRepository,
    *,
    model_config_id: UUID,
    tenant_id: UUID,
    runtime_options: ModelRuntimeOptions | None = None,
) -> ResolvedModelConfig:
    config = repository.get_model_config(model_config_id, tenant_id)
    if config is None:
        raise ModelConfigNotFoundError(model_config_id)
    _validate_config_access(config, tenant_id)
    if config.provider is ModelProvider.SPEEDBEAR and config.is_public:
        binding = repository.get_public_binding(tenant_id, ModelProvider.SPEEDBEAR)
        if binding is None:
            raise PublicCredentialUnavailableError(model_config_id, tenant_id)
        return _build_from_binding(config, binding, tenant_id, runtime_options)
    return _build_from_key(
        config,
        _select_key(config, repository.list_active_keys(model_config_id)),
        tenant_id,
        runtime_options,
    )


async def resolve_model_async(
    repository: AsyncModelRegistryRepository,
    *,
    model_config_id: UUID,
    tenant_id: UUID,
    runtime_options: ModelRuntimeOptions | None = None,
) -> ResolvedModelConfig:
    config = await repository.get_model_config(model_config_id, tenant_id)
    if config is None:
        raise ModelConfigNotFoundError(model_config_id)
    _validate_config_access(config, tenant_id)
    if config.provider is ModelProvider.SPEEDBEAR and config.is_public:
        binding = await repository.get_public_binding(
            tenant_id,
            ModelProvider.SPEEDBEAR,
        )
        if binding is None:
            raise PublicCredentialUnavailableError(model_config_id, tenant_id)
        return _build_from_binding(config, binding, tenant_id, runtime_options)
    keys = await repository.list_active_keys(model_config_id)
    return _build_from_key(
        config,
        _select_key(config, keys),
        tenant_id,
        runtime_options,
    )


def record_model_usage(
    repository: ModelRegistryRepository,
    *,
    key_id: UUID | None,
) -> None:
    if key_id is None:
        return
    try:
        repository.record_key_usage(key_id)
    except Exception as exc:
        raise ModelUsageRecordError(key_id, exc) from exc


async def record_model_usage_async(
    repository: AsyncModelRegistryRepository,
    *,
    key_id: UUID | None,
) -> None:
    if key_id is None:
        return
    try:
        await repository.record_key_usage(key_id)
    except Exception as exc:
        raise ModelUsageRecordError(key_id, exc) from exc
