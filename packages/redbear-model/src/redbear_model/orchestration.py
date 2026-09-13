"""请求内换渠道编排（spec §11.2，非流式）。

沿 resolver 有序候选链执行单次调用：瞬时网络（connection/timeout）同渠道重试、
可换渠道错误（429/鉴权失败/5xx/凭据解密失败）顺延下一候选、400 类业务错误与
不可分类异常原样透传、候选耗尽聚合报错。usage 事件发布不在此模块（M3 runtime
gate 接入点，编排只保证错误语义与 attempts 链数据可推导）。

流式场景的"产出前失败才整体重试"自写循环在 M3 套入本编排（spec §11.2）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from .contracts import ChannelSnapshot, ModelConfigSnapshot, ResolvedModelConfig
from .crypto import CredentialCipher
from .errors import (
    ChannelSwitchExhaustedError,
    CredentialDecryptError,
    NoAvailableChannelError,
)
from .resolver import build_resolved_from_channel

R = TypeVar("R")

_TRANSIENT_NAME_TOKENS = (
    "connection",
    "connect",
    "timeout",
    "reset",
    "unreachable",
    "networkerror",
)


def _causes(exc: BaseException):
    """沿 __cause__/__context__ 链遍历（含头部，去重，深度上限 8）。"""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    for _ in range(8):
        if not pending:
            return
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for wrapped in (current.__cause__, current.__context__):
            if isinstance(wrapped, BaseException):
                pending.append(wrapped)


def _http_status(exc: BaseException) -> int | None:
    for current in _causes(exc):
        status = getattr(current, "status_code", None)
        if status is None:
            response = getattr(current, "response", None)
            if isinstance(response, dict):
                status = response.get("status_code")
            else:
                status = getattr(response, "status_code", None)
        try:
            if status is not None:
                return int(status)
        except (TypeError, ValueError):
            continue
    return None


def is_transient_channel_error(exc: BaseException) -> bool:
    """瞬时网络（connection/timeout/连接重置，spec §11.2 首行）→ 同渠道重试。

    整条异常链带明确 HTTP 状态码的失败不属瞬时网络（429/5xx 走 switchable 判定），
    避免两谓词对同一异常误判错序。
    """
    if _http_status(exc) is not None:
        return False
    for current in _causes(exc):
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        name = type(current).__name__.lower()
        if any(token in name for token in _TRANSIENT_NAME_TOKENS):
            return True
    return False


def is_switchable_channel_error(exc: BaseException) -> bool:
    """可换渠道（429/5xx/鉴权失败/凭据无效，spec §11.2 次行）→ 顺延下一候选。"""
    for current in _causes(exc):
        if isinstance(current, CredentialDecryptError):
            return True
    status = _http_status(exc)
    return status is not None and (status in (401, 403, 429) or status >= 500)


def is_terminal_channel_error(exc: BaseException) -> bool:
    """不可重试（400 类业务错误：内容审查/上下文超长/模型不存在）→ 直接透传。"""
    status = _http_status(exc)
    return (
        status is not None
        and 400 <= status < 500
        and status not in (401, 403, 429)
    )


def run_with_channel_fallback(
    config: ModelConfigSnapshot,
    ordered_channels: list[ChannelSnapshot],
    *,
    tenant_id: UUID,
    cipher: CredentialCipher,
    invoke: Callable[[ResolvedModelConfig], R],
    model_name: str | None = None,
    is_candidate_available: Callable[[ChannelSnapshot], bool] | None = None,
    max_attempts_per_channel: int = 3,
) -> R:
    """§11.2 非流式编排：返回首个渠道调用成功结果，失败按错误分类换渠道。

    ordered_channels 来自 resolver v2 有序候选链（调用方传整链供顺延；空/全被
    cooldown 过滤 → NoAvailableChannelError）。每渠道至多尝试
    max_attempts_per_channel 次（默认 3 = 1 首发 + 2 瞬时重试，spec §11.2
    "每候选重试 2 次"；总量 = 候选数 × 每候选尝试，宿主可调）。invoke 抛
    terminal/不可分类异常 → 原样透传（不吞错）；全耗尽 →
    ChannelSwitchExhaustedError（failures = 渠道链 + 错误类型名，原始错误挂 __cause__）。
    """
    anchor = model_name or config.name
    available = (
        is_candidate_available
        if is_candidate_available is not None
        else (lambda _channel: True)
    )
    failures: list[tuple[UUID | None, str]] = []
    last_error: BaseException | None = None
    attempted = False
    for channel in ordered_channels:
        if not available(channel):
            continue
        attempted = True
        for attempt in range(max_attempts_per_channel):
            try:
                resolved = build_resolved_from_channel(
                    config,
                    channel,
                    tenant_id=tenant_id,
                    model_name=anchor,
                    cipher=cipher,
                )
                return invoke(resolved)
            except CredentialDecryptError as exc:  # 凭据无效：立即顺延，不占瞬时重试预算
                last_error = exc
                failures.append((channel.id, type(exc).__name__))
                break
            except Exception as exc:
                last_error = exc
                if is_transient_channel_error(exc):
                    if attempt < max_attempts_per_channel - 1:
                        continue
                    failures.append((channel.id, type(exc).__name__))
                    break
                if is_switchable_channel_error(exc):
                    failures.append((channel.id, type(exc).__name__))
                    break
                raise  # terminal 或不可分类：原样透传
    if not attempted:
        raise NoAvailableChannelError(
            config.model_config_id,
            config.provider.value,
            anchor,
            "empty channel chain or all filtered by cooldown",
        )
    raise ChannelSwitchExhaustedError(
        config.model_config_id,
        config.provider.value,
        anchor,
        failures,
        last_error,
    )


async def run_with_channel_fallback_async(
    config: ModelConfigSnapshot,
    ordered_channels: list[ChannelSnapshot],
    *,
    tenant_id: UUID,
    cipher: CredentialCipher,
    invoke: Callable[[ResolvedModelConfig], Awaitable[R]],
    model_name: str | None = None,
    is_candidate_available: Callable[[ChannelSnapshot], bool] | None = None,
    max_attempts_per_channel: int = 3,
) -> R:
    """run_with_channel_fallback 的 async 态（异步宿主运行解析面，GC#11）。"""
    anchor = model_name or config.name
    available = (
        is_candidate_available
        if is_candidate_available is not None
        else (lambda _channel: True)
    )
    failures: list[tuple[UUID | None, str]] = []
    last_error: BaseException | None = None
    attempted = False
    for channel in ordered_channels:
        if not available(channel):
            continue
        attempted = True
        for attempt in range(max_attempts_per_channel):
            try:
                resolved = build_resolved_from_channel(
                    config,
                    channel,
                    tenant_id=tenant_id,
                    model_name=anchor,
                    cipher=cipher,
                )
                return await invoke(resolved)
            except CredentialDecryptError as exc:
                last_error = exc
                failures.append((channel.id, type(exc).__name__))
                break
            except Exception as exc:
                last_error = exc
                if is_transient_channel_error(exc):
                    if attempt < max_attempts_per_channel - 1:
                        continue
                    failures.append((channel.id, type(exc).__name__))
                    break
                if is_switchable_channel_error(exc):
                    failures.append((channel.id, type(exc).__name__))
                    break
                raise
    if not attempted:
        raise NoAvailableChannelError(
            config.model_config_id,
            config.provider.value,
            anchor,
            "empty channel chain or all filtered by cooldown",
        )
    raise ChannelSwitchExhaustedError(
        config.model_config_id,
        config.provider.value,
        anchor,
        failures,
        last_error,
    )
