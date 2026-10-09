"""Pure model visibility, credential selection, and resolution rules."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import SecretStr

from .contracts import (
    ChannelSnapshot,
    ChannelSource,
    ModelConfigSnapshot,
    ModelProfile,
    ModelProvider,
    ModelRuntimeOptions,
    ResolvedModelConfig,
)
from .crypto import CredentialCipher
from .errors import (
    CredentialDecryptError,
    ModelAccessDeniedError,
    ModelConfigDeprecatedError,
    ModelConfigInactiveError,
    NoAvailableChannelError,
    SpeedbearChannelMissingError,
)
from .runtime.flags import normalize_runtime_flags

logger = logging.getLogger(__name__)


def _validate_config_access(
    config: ModelConfigSnapshot,
    tenant_id: UUID,
) -> None:
    if config.is_deprecated:
        raise ModelConfigDeprecatedError(config.model_config_id)
    if not config.is_active:
        raise ModelConfigInactiveError(config.model_config_id)
    if config.tenant_id != tenant_id and not config.is_public:
        raise ModelAccessDeniedError(config.model_config_id, tenant_id)


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
    profile: ModelProfile,
    params: dict,
    runtime_options: ModelRuntimeOptions | None,
    channel_id: UUID | None = None,
) -> ResolvedModelConfig:
    deep_thinking, thinking_budget, json_output = _runtime_flags(params)
    deep_thinking, thinking_budget, json_output = normalize_runtime_flags(
        profile.features,
        deep_thinking,
        thinking_budget,
        json_output,
        model_name,
    )
    return ResolvedModelConfig(
        model_config_id=config.model_config_id,
        key_id=key_id,
        channel_id=channel_id,
        tenant_id=tenant_id,
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        profile=profile,
        deep_thinking=deep_thinking,
        thinking_budget_tokens=thinking_budget,
        json_output=json_output,
        provider_params=params,
        runtime=runtime_options or ModelRuntimeOptions(),
    )


# ---- v2 渠道解析核（spec §10.1/§11.1）：输入为本租户活跃渠道快照池，纯函数无 I/O ----


def match_channel_candidates(
    config: ModelConfigSnapshot,
    channels: Sequence[ChannelSnapshot],
    *,
    model_name: str,
) -> list[ChannelSnapshot]:
    """§10.1.3 自动匹配：provider 相等 && is_active && 覆盖集命中（[]=全量，点名=仅列出名）。

    channels 应为 registry 返回的该租户活跃渠道池（含全部 provider）；source=platform 渠道
    仅出现在 provider=speedbear 池（租户自持，无跨租户泄漏问题），普通匹配不做 source 排除。
    """
    return [
        ch
        for ch in channels
        if ch.provider == config.provider
        and ch.is_active
        and ch.covers(model_name)
    ]


def match_platform_speedbear_channels(
    channels: Sequence[ChannelSnapshot],
) -> list[ChannelSnapshot]:
    """§10.1.4 speedbear 公共模型候选：provider=speedbear && source=platform，不收窄 model_names。"""
    return [
        ch
        for ch in channels
        if ch.provider == ModelProvider.SPEEDBEAR
        and ch.source == ChannelSource.PLATFORM
        and ch.is_active
    ]


def order_channel_candidates(
    candidates: Sequence[ChannelSnapshot],
    *,
    model_name: str,
    loads: Mapping[UUID, int] | None = None,
) -> list[ChannelSnapshot]:
    """§11.1 选路排序：点名（model_names 含模型名）> provider 级 → priority desc → least-used
    → created_at asc（created_at 相同按 id 定序）。返回整条有序链：编排失败换渠道沿链向后走。

    loads 为渠道 id → 滚动窗口用量（宿主从 usage_records 派生）；None/缺省视为 0，
    即 M4 计量落地前退化为先登记优先。
    """
    return sorted(
        candidates,
        key=lambda ch: (
            0 if model_name in ch.model_names else 1,
            -ch.priority,
            0 if loads is None else loads.get(ch.id, 0),
            ch.created_at_ms,
            str(ch.id),
        ),
    )


def build_resolved_from_channel(
    config: ModelConfigSnapshot,
    channel: ChannelSnapshot,
    *,
    tenant_id: UUID,
    model_name: str,
    cipher: CredentialCipher,
    runtime_options: ModelRuntimeOptions | None = None,
) -> ResolvedModelConfig:
    """解密命中渠道凭据并组 ResolvedModelConfig（解密收敛点；失败抛 CredentialDecryptError）。"""
    try:
        plaintext = cipher.decrypt(
            channel.credential_encrypted,
            aad=f"{channel.provider}:{tenant_id}",
        )
    except Exception as exc:
        raise CredentialDecryptError(channel.id, channel.provider, exc) from exc
    return _build_resolved(
        config,
        key_id=None,
        channel_id=channel.id,
        tenant_id=tenant_id,
        provider=config.provider,
        model_name=model_name,
        api_key=SecretStr(plaintext),
        base_url=channel.api_base,
        profile=config.profile,
        params=dict(config.config),
        runtime_options=runtime_options,
    )


def ordered_channel_candidates(
    config: ModelConfigSnapshot,
    channels: Sequence[ChannelSnapshot],
    *,
    tenant_id: UUID,
    model_name: str | None = None,
    loads: Mapping[UUID, int] | None = None,
    validate_access: bool = True,
) -> list[ChannelSnapshot]:
    """§10.1/§11.1 有序候选链（纯函数）：access 校验 + 分支匹配 + 选路排序，空链不抛。

    speedbear 公共 config 走平台渠道候选（不收窄 model_names），其余按 provider 相等 +
    覆盖集命中（锚点名 = 显式 model_name or config.name）；组合 config 无单渠道链
    （成员编排在 composite 模块），直接拒绝。宿主 failover plan 构建消费整链；
    validate_access=False 供探测路径复用（inactive 不报错，空链自解释）。
    """
    if validate_access:
        _validate_config_access(config, tenant_id)
    anchor = model_name or config.name
    if config.provider is ModelProvider.COMPOSITE:
        raise NoAvailableChannelError(
            config.model_config_id,
            str(config.provider),
            anchor,
            "composite config resolves via member orchestration only",
        )
    if config.provider is ModelProvider.SPEEDBEAR and config.is_public:
        candidates = match_platform_speedbear_channels(channels)
    else:
        candidates = match_channel_candidates(config, channels, model_name=anchor)
    return order_channel_candidates(candidates, model_name=anchor, loads=loads)


def resolve_and_chain_from_pool(
    config: ModelConfigSnapshot,
    channels: Sequence[ChannelSnapshot],
    *,
    tenant_id: UUID,
    cipher: CredentialCipher,
    model_name: str | None = None,
    runtime_options: ModelRuntimeOptions | None = None,
    loads: Mapping[UUID, int] | None = None,
) -> tuple[ResolvedModelConfig, list[ChannelSnapshot]]:
    """v2 解析门面（整链版）：返回 (首个可解密候选 resolved, 自该位起的有序切片)——failover 宿主一次拿齐。

    候选逐个尝试解密、跳过坏密文（与组合 resolve_composite_head 对齐；运行期编排层本就
    顺延坏密文候选，构建期不再提前中止），切片头部恒为实际首发渠道。全候选失败抛首个
    CredentialDecryptError（宿主按模式映射：only → 4014；prefer → 记 fallback 走旧表）。
    其余错误与 resolve_from_channel_pool 同形（speedbear 公共空链 → SpeedbearChannelMissingError，
    其余空链 → NoAvailableChannelError）。
    """
    anchor = model_name or config.name
    ordered = ordered_channel_candidates(
        config, channels, tenant_id=tenant_id, model_name=model_name, loads=loads
    )
    if not ordered:
        if config.provider is ModelProvider.SPEEDBEAR and config.is_public:
            raise SpeedbearChannelMissingError(config.model_config_id, tenant_id)
        raise NoAvailableChannelError(
            config.model_config_id,
            str(config.provider),
            anchor,
        )
    failures: list[CredentialDecryptError] = []
    for index, channel in enumerate(ordered):
        try:
            resolved = build_resolved_from_channel(
                config,
                channel,
                tenant_id=tenant_id,
                model_name=anchor,
                cipher=cipher,
                runtime_options=runtime_options,
            )
        except CredentialDecryptError as exc:
            logger.warning(
                "config %s channel %s credential decrypt failed, trying next: %s",
                config.model_config_id,
                channel.id,
                exc,
            )
            failures.append(exc)
            continue
        return resolved, ordered[index:]
    raise failures[0]


def resolve_from_channel_pool(
    config: ModelConfigSnapshot,
    channels: Sequence[ChannelSnapshot],
    *,
    tenant_id: UUID,
    cipher: CredentialCipher,
    model_name: str | None = None,
    runtime_options: ModelRuntimeOptions | None = None,
    loads: Mapping[UUID, int] | None = None,
) -> ResolvedModelConfig:
    """v2 解析门面（M3 宿主把 resolve_model 内部切到这里）。

    锚点名 = 显式 model_name（组合编排传成员声明名）or config.name（普通模型真实调用名）。
    组合 config 无单渠道解析（成员编排在 composite 模块），直接命中此处视为调用方错误。
    委托 resolve_and_chain_from_pool 取首元素（坏密文顺延，错误类型/入参不变）。
    """
    resolved, _chain = resolve_and_chain_from_pool(
        config,
        channels,
        tenant_id=tenant_id,
        cipher=cipher,
        model_name=model_name,
        runtime_options=runtime_options,
        loads=loads,
    )
    return resolved
