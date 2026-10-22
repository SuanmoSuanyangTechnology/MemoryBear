"""Host-side failover consumption: FailoverPlan -> redbear_model orchestration -> per-candidate config rebuild.

The plan is built by the resolved-config side (no plaintext / no cipher / no Session); the
cipher is injected here on demand (a process-local singleton). The facade's first candidate
reuses the passed-in config instance; only after switching to a later channel is the
underlying model rebuilt via attempt_config. The facade also handles per-call accounting and
reporting attribution (FailoverStats): usage/gateway events follow the candidate actually
hit (a supply-side snapshot of the resolved config), with zero rebuilds when no switch
occurs.
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

from ..errors import BizCode
from ..errors import BusinessException
from .model_provider_config import get_default_provider_api_base
from .base import RedBearModelConfig, RedBearModelFactory

R = TypeVar("R")

# Excluded when rebuilding provider params: config-only fields + composite member
# declarations (must not leak into extra_params pass-through)
_ATTEMPT_PARAM_EXCLUDES = frozenset(RedBearModelFactory._CONFIG_ONLY_KEYS) | {"members"}


class FailoverStats:
    """Per-call failover attribution (held locally, never crossing requests via a shared config).

    attempts = provider call count (reportable even when orchestration fails); switched =
    the winning candidate is not the first; last = the final candidate that entered a
    provider call (usage/gateway reporting attribution, see attribution_config).
    Accounting is handled centrally by the facade in run_plan/run_plan_async (when stats is
    non-null); call sites never hand-write it.
    """

    __slots__ = ("attempts", "switched", "last")

    def __init__(self) -> None:
        self.attempts = 0
        self.switched = False
        self.last: ResolvedModelConfig | None = None

    def counted(self) -> int:
        """attempts count for usage events: a call that never reached a provider counts as 1 (existing semantics)."""
        return self.attempts or 1

    def attribution_config(self, base: RedBearModelConfig) -> RedBearModelConfig:
        """Reporting-attribution config: without a switch (no plan / first candidate) return base itself; after a switch or exhausted failure return a rebuild of the candidate hit.

        Event provider/model_name/channel_id carry the supply-side snapshot actually hit;
        channel_id is also the least-used derivation key -- after a switch it must follow
        the channel actually invoked.
        """
        if self.last is None or is_initial_candidate(base, self.last):
            return base
        return attempt_config(base, self.last)


def is_initial_candidate(base: RedBearModelConfig, resolved: ResolvedModelConfig) -> bool:
    """Whether resolved hits the plan's first candidate (the sole predicate for "first candidate reuses the passed-in instance", shared by three call faces)."""
    plan = base.failover_plan
    first = plan.candidates[0] if plan and plan.candidates else None
    return (
        first is not None
        and resolved.channel_id == first.channel.id
        and resolved.model_name == first.anchor
    )


def attempt_config(base: RedBearModelConfig, resolved: ResolvedModelConfig) -> RedBearModelConfig:
    """Rebuild the config from the winning candidate: call-face fields come from resolved; timeout/concurrency/caller extra_params are preserved as-is.

    Uses model_copy (skipping validators to avoid duplicate warnings); provider_params are
    merged with the caller's extra_params after excluding config-only keys (the caller wins
    on name conflicts).
    """
    provider_params = {
        key: value
        for key, value in (resolved.provider_params or {}).items()
        if key not in _ATTEMPT_PARAM_EXCLUDES
    }
    return base.model_copy(
        update={
            "model_name": resolved.model_name,
            "provider": str(resolved.provider),
            "api_key": resolved.api_key.get_secret_value(),
            "base_url": resolved.base_url
            or get_default_provider_api_base(resolved.provider, resolved.profile.type),
            "input_modalities": [str(item) for item in resolved.profile.input_modalities],
            "output_modalities": [str(item) for item in resolved.profile.output_modalities],
            "features": [str(item) for item in resolved.profile.features],
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
    """Sync facade entry: plan + per-candidate callback (the callback receives resolved and builds/reuses the underlying model instance).

    When stats is non-null the facade keeps the accounting (attempts/last/switched); the
    callback only invokes.
    """
    from ..services.channel_service import cipher_from_env

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
    """Async counterpart of run_plan (async facades use this entry; calling the sync orchestration from async code is forbidden)."""
    from ..services.channel_service import cipher_from_env

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
    """Channel chain exhausted / empty -> NO_AVAILABLE_CHANNEL business error (HTTP boundary mapping; the message contains no credentials)."""
    return BusinessException(str(exc), BizCode.NO_AVAILABLE_CHANNEL)
