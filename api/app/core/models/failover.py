"""宿主门面换渠道消费（spec §11.2）：FailoverPlan → redbear_model 编排 → 逐候选重建配置。

plan 由解析面构建（零明文/零 cipher/零 Session），cipher 在此按需注入（进程内单例）；
门面首候选沿用入参配置实例，仅在顺延换渠道后经 attempt_config 重建底层模型。
门面同时承担调用级记账与上报归属（FailoverStats）：usage/网关事件跟随实际命中候选
（spec §13.1 供应面快照），未换渠道时零重建。
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from redbear_model import (
    FallbackOutcome,
    FailoverPlan,
    ResolvedModelConfig,
    run_failover_plan,
    run_failover_plan_async,
)

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.model_provider_config import get_default_provider_api_base
from app.core.models.base import RedBearModelConfig, RedBearModelFactory

R = TypeVar("R")

# provider 参数重建时排除：配置专有字段 + 组合成员声明（不得混入 extra_params 透传）
_ATTEMPT_PARAM_EXCLUDES = frozenset(RedBearModelFactory._CONFIG_ONLY_KEYS) | {"members"}


class FailoverStats:
    """调用级换渠道归因（局部持有，不随共享 config 跨请求）。

    attempts = provider 调用次数（编排失败时也可上报）；switched = 胜出候选非首候选；
    last = 最后进入 provider 调用的候选（usage/网关上报归属，见 attribution_config）。
    记账由门面 run_plan/run_plan_async 统一完成（stats 非空时），调用点不手写。
    """

    __slots__ = ("attempts", "switched", "last")

    def __init__(self) -> None:
        self.attempts = 0
        self.switched = False
        self.last: ResolvedModelConfig | None = None

    def counted(self) -> int:
        """usage 事件 attempts 口径：未走进 provider 的调用记 1（现状语义）。"""
        return self.attempts or 1

    def attribution_config(self, base: RedBearModelConfig) -> RedBearModelConfig:
        """上报归属配置：未换渠道（含无 plan/首候选）返回 base 本身，换渠道或失败耗尽返回命中候选重建。

        spec §13.1：事件 provider/model_name/channel_id 承载实际命中的供应面快照，
        channel_id 同时是 least-used 派生键（§11.1）——换渠道后必须随实际调用渠道走。
        """
        if self.last is None or is_initial_candidate(base, self.last):
            return base
        return attempt_config(base, self.last)


def is_initial_candidate(base: RedBearModelConfig, resolved: ResolvedModelConfig) -> bool:
    """resolved 是否命中 plan 首候选（「首候选复用入参实例」的唯一判定契约，三处调用面共用）。"""
    plan = base.failover_plan
    first = plan.candidates[0] if plan and plan.candidates else None
    return (
        first is not None
        and resolved.channel_id == first.channel.id
        and resolved.model_name == first.anchor
    )


def attempt_config(base: RedBearModelConfig, resolved: ResolvedModelConfig) -> RedBearModelConfig:
    """按胜出候选重建配置：调用面字段取 resolved，超时/并发/调用方 extra_params 原样保留。

    走 model_copy（跳校验器，避免重复告警）；provider_params 排除配置专有键后与
    调用方 extra_params 合并（调用方同名优先）。
    """
    provider_params = {
        key: value
        for key, value in (resolved.provider_params or {}).items()
        if key not in _ATTEMPT_PARAM_EXCLUDES
    }
    capabilities, is_omni = resolved.profile.legacy_capability_view(resolved.provider)
    return base.model_copy(
        update={
            "model_name": resolved.model_name,
            "provider": str(resolved.provider),
            "api_key": resolved.api_key.get_secret_value(),
            "base_url": resolved.base_url
            or get_default_provider_api_base(resolved.provider, resolved.profile.type),
            "capability": [str(item) for item in capabilities],
            "is_omni": is_omni,
            "deep_thinking": resolved.deep_thinking,
            "thinking_budget_tokens": resolved.thinking_budget_tokens,
            "json_output": resolved.json_output,
            "tenant_id": str(resolved.tenant_id),
            "model_config_id": str(resolved.model_config_id),
            "channel_id": None if resolved.channel_id is None else str(resolved.channel_id),
            "extra_params": {**provider_params, **base.extra_params},
        }
    )


def run_plan(
    plan: FailoverPlan,
    invoke: Callable[[ResolvedModelConfig], R],
    *,
    stats: FailoverStats | None = None,
) -> FallbackOutcome[R]:
    """同步门面入口：plan + 逐候选回调（回调收 resolved，自建/复用底层模型实例）。

    stats 非空时门面统一记账（attempts/last/switched），回调只做调用。
    """
    from app.services.channel_service import cipher_from_env

    def _attempt(resolved: ResolvedModelConfig) -> R:
        if stats is not None:
            stats.attempts += 1
            stats.last = resolved
        return invoke(resolved)

    outcome = run_failover_plan(plan, cipher=cipher_from_env(), invoke=_attempt)
    if stats is not None:
        stats.switched = outcome.switched
    return outcome


async def run_plan_async(
    plan: FailoverPlan,
    invoke: Callable[[ResolvedModelConfig], Any],
    *,
    stats: FailoverStats | None = None,
) -> FallbackOutcome[Any]:
    """run_plan 的 async 态（异步门面走本入口，禁止 async 内调 sync 编排）。"""
    from app.services.channel_service import cipher_from_env

    async def _attempt(resolved: ResolvedModelConfig) -> Any:
        if stats is not None:
            stats.attempts += 1
            stats.last = resolved
        return await invoke(resolved)

    outcome = await run_failover_plan_async(plan, cipher=cipher_from_env(), invoke=_attempt)
    if stats is not None:
        stats.switched = outcome.switched
    return outcome


def channel_error_to_business(exc: BaseException) -> BusinessException:
    """渠道链耗尽/空链 → NO_AVAILABLE_CHANNEL 业务错误（HTTP 边界映射；文案不含凭据）。"""
    return BusinessException(str(exc), BizCode.NO_AVAILABLE_CHANNEL)
