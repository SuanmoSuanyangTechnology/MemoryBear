"""请求内换渠道编排（spec §11.2）。

沿 resolver 有序候选链执行单次调用：瞬时网络（connection/timeout）同渠道重试、
可换渠道错误（429/鉴权失败/5xx/凭据解密失败）顺延下一候选、400 类业务错误与
不可分类异常原样透传、候选耗尽聚合报错。usage 事件发布不在此模块（宿主从
`FallbackOutcome` 派生 attempts/fallback 归因）。

宿主门面走 pair 入口：`FailoverPlan`（入口快照 + 密文候选链）→ `run_failover_plan`；
单 config + 渠道链形状保留为兼容口（`run_with_channel_fallback`，membranes 组合编排
成员传 `model_name` 锚点）。流式"产出前失败才整体重试"由宿主在 invoke 回调内
eager 拉首块实现（首块产出后不再进入本编排）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar
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


@dataclass(frozen=True)
class FailoverCandidate:
    """候选对：模型 config 快照 + 命中渠道。anchor = model_name or config.name（调用锚点名）。"""

    config: ModelConfigSnapshot
    channel: ChannelSnapshot
    model_name: str | None = None

    @property
    def anchor(self) -> str:
        return self.model_name or self.config.name


@dataclass(frozen=True)
class FailoverPlan:
    """请求内换渠道计划：入口快照 + 密文候选链（零明文、零 cipher、零 Session）。

    宿主解析面构建（复合链切片后 candidates[0] 恒为壳渠道），门面消费时注入 cipher 执行。
    空链/耗尽的错误身份取 entry（复合传组合 config）。
    """

    entry: ModelConfigSnapshot
    tenant_id: UUID
    candidates: tuple[FailoverCandidate, ...]


@dataclass(frozen=True)
class FallbackOutcome(Generic[R]):
    """编排成功结果与归因：attempts = invoke 调用次数，switched = 胜出候选非首个被尝试者。"""

    result: R
    resolved: ResolvedModelConfig
    attempts: int
    switched: bool
    failures: tuple[tuple[UUID | None, str], ...]


def run_candidate_fallback(
    candidates: Sequence[FailoverCandidate],
    *,
    entry: ModelConfigSnapshot,
    tenant_id: UUID,
    cipher: CredentialCipher,
    invoke: Callable[[ResolvedModelConfig], R],
    is_candidate_available: Callable[[FailoverCandidate], bool] | None = None,
    max_attempts_per_channel: int = 3,
) -> FallbackOutcome[R]:
    """§11.2 非流式 pair 编排：沿候选链执行，返回首个成功结果与归因。

    与 run_with_channel_fallback 同语义，差异是候选各带自己的 config（复合链成员异构）。
    瞬时网络错误同候选重试（至多 max_attempts_per_channel 次 = 1 首发 + 2 重试），
    可换渠道错误顺延下一候选，terminal/不可分类原样透传；全耗尽 →
    ChannelSwitchExhaustedError。空链/全被过滤 → NoAvailableChannelError（用 entry 身份）。
    attempts 计 invoke 次数（解密失败不占），switched 指同渠道瞬时重试之外的换渠道。
    """
    available = (
        is_candidate_available
        if is_candidate_available is not None
        else (lambda _candidate: True)
    )
    failures: list[tuple[UUID | None, str]] = []
    last_error: BaseException | None = None
    attempted = False
    attempts = 0
    first_attempted: int | None = None
    for index, candidate in enumerate(candidates):
        if not available(candidate):
            continue
        attempted = True
        if first_attempted is None:
            first_attempted = index
        for attempt in range(max_attempts_per_channel):
            try:
                resolved = build_resolved_from_channel(
                    candidate.config,
                    candidate.channel,
                    tenant_id=tenant_id,
                    model_name=candidate.anchor,
                    cipher=cipher,
                )
                attempts += 1
                result = invoke(resolved)
                return FallbackOutcome(
                    result=result,
                    resolved=resolved,
                    attempts=attempts,
                    switched=index != first_attempted,
                    failures=tuple(failures),
                )
            except CredentialDecryptError as exc:  # 凭据无效：立即顺延，不占瞬时重试预算
                last_error = exc
                failures.append((candidate.channel.id, type(exc).__name__))
                break
            except Exception as exc:
                last_error = exc
                if is_transient_channel_error(exc):
                    if attempt < max_attempts_per_channel - 1:
                        continue
                    failures.append((candidate.channel.id, type(exc).__name__))
                    break
                if is_switchable_channel_error(exc):
                    failures.append((candidate.channel.id, type(exc).__name__))
                    break
                raise  # terminal 或不可分类：原样透传
    if not attempted:
        raise NoAvailableChannelError(
            entry.model_config_id,
            entry.provider.value,
            entry.name,
            "empty channel chain or all filtered by cooldown",
        )
    raise ChannelSwitchExhaustedError(
        entry.model_config_id,
        entry.provider.value,
        entry.name,
        failures,
        last_error,
    )


async def run_candidate_fallback_async(
    candidates: Sequence[FailoverCandidate],
    *,
    entry: ModelConfigSnapshot,
    tenant_id: UUID,
    cipher: CredentialCipher,
    invoke: Callable[[ResolvedModelConfig], Awaitable[R]],
    is_candidate_available: Callable[[FailoverCandidate], bool] | None = None,
    max_attempts_per_channel: int = 3,
) -> FallbackOutcome[R]:
    """run_candidate_fallback 的 async 态（异步宿主运行解析面，GC#11）。"""
    available = (
        is_candidate_available
        if is_candidate_available is not None
        else (lambda _candidate: True)
    )
    failures: list[tuple[UUID | None, str]] = []
    last_error: BaseException | None = None
    attempted = False
    attempts = 0
    first_attempted: int | None = None
    for index, candidate in enumerate(candidates):
        if not available(candidate):
            continue
        attempted = True
        if first_attempted is None:
            first_attempted = index
        for attempt in range(max_attempts_per_channel):
            try:
                resolved = build_resolved_from_channel(
                    candidate.config,
                    candidate.channel,
                    tenant_id=tenant_id,
                    model_name=candidate.anchor,
                    cipher=cipher,
                )
                attempts += 1
                result = await invoke(resolved)
                return FallbackOutcome(
                    result=result,
                    resolved=resolved,
                    attempts=attempts,
                    switched=index != first_attempted,
                    failures=tuple(failures),
                )
            except CredentialDecryptError as exc:
                last_error = exc
                failures.append((candidate.channel.id, type(exc).__name__))
                break
            except Exception as exc:
                last_error = exc
                if is_transient_channel_error(exc):
                    if attempt < max_attempts_per_channel - 1:
                        continue
                    failures.append((candidate.channel.id, type(exc).__name__))
                    break
                if is_switchable_channel_error(exc):
                    failures.append((candidate.channel.id, type(exc).__name__))
                    break
                raise
    if not attempted:
        raise NoAvailableChannelError(
            entry.model_config_id,
            entry.provider.value,
            entry.name,
            "empty channel chain or all filtered by cooldown",
        )
    raise ChannelSwitchExhaustedError(
        entry.model_config_id,
        entry.provider.value,
        entry.name,
        failures,
        last_error,
    )


def run_failover_plan(
    plan: FailoverPlan,
    *,
    cipher: CredentialCipher,
    invoke: Callable[[ResolvedModelConfig], R],
    is_candidate_available: Callable[[FailoverCandidate], bool] | None = None,
    max_attempts_per_channel: int = 3,
) -> FallbackOutcome[R]:
    """宿主门面入口：FailoverPlan → 编排（cipher 由消费方注入，plan 本身上下文无关）。"""
    return run_candidate_fallback(
        plan.candidates,
        entry=plan.entry,
        tenant_id=plan.tenant_id,
        cipher=cipher,
        invoke=invoke,
        is_candidate_available=is_candidate_available,
        max_attempts_per_channel=max_attempts_per_channel,
    )


async def run_failover_plan_async(
    plan: FailoverPlan,
    *,
    cipher: CredentialCipher,
    invoke: Callable[[ResolvedModelConfig], Awaitable[R]],
    is_candidate_available: Callable[[FailoverCandidate], bool] | None = None,
    max_attempts_per_channel: int = 3,
) -> FallbackOutcome[R]:
    """run_failover_plan 的 async 态。"""
    return await run_candidate_fallback_async(
        plan.candidates,
        entry=plan.entry,
        tenant_id=plan.tenant_id,
        cipher=cipher,
        invoke=invoke,
        is_candidate_available=is_candidate_available,
        max_attempts_per_channel=max_attempts_per_channel,
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
    """§11.2 非流式编排（兼容口）：单 config + 渠道链形状，返回首个成功结果。

    内部构造 FailoverCandidate 走 run_candidate_fallback（签名与返回类型保持不变，
    membranes 组合编排成员传 model_name 锚点）。空/全被 cooldown 过滤 →
    NoAvailableChannelError；每渠道至多 max_attempts_per_channel 次（默认 3 = 1 首发
    + 2 瞬时重试，spec §11.2 "每候选重试 2 次"）；全耗尽 → ChannelSwitchExhaustedError
    （failures = 渠道链 + 错误类型名，原始错误挂 __cause__）。
    """
    hook = (
        None
        if is_candidate_available is None
        else (lambda candidate: is_candidate_available(candidate.channel))
    )
    outcome = run_candidate_fallback(
        [
            FailoverCandidate(config=config, channel=channel, model_name=model_name)
            for channel in ordered_channels
        ],
        entry=config,
        tenant_id=tenant_id,
        cipher=cipher,
        invoke=invoke,
        is_candidate_available=hook,
        max_attempts_per_channel=max_attempts_per_channel,
    )
    return outcome.result


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
    """run_with_channel_fallback 的 async 态（异步宿主运行解析面，GC#11）。

    内部构造 FailoverCandidate 走 run_candidate_fallback_async；签名/返回类型不变。
    """
    hook = (
        None
        if is_candidate_available is None
        else (lambda candidate: is_candidate_available(candidate.channel))
    )
    outcome = await run_candidate_fallback_async(
        [
            FailoverCandidate(config=config, channel=channel, model_name=model_name)
            for channel in ordered_channels
        ],
        entry=config,
        tenant_id=tenant_id,
        cipher=cipher,
        invoke=invoke,
        is_candidate_available=hook,
        max_attempts_per_channel=max_attempts_per_channel,
    )
    return outcome.result
