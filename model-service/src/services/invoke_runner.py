"""Invoke call planning: timeout tiers plus the facade call bound to one request.

The HTTP endpoint and the gRPC servicer share this module so both faces pick
the same tiers and hand the facade the same arguments. ``invoke_call`` is
resolved by the caller (the HTTP route keeps its module-level ``invoke`` name
patchable) rather than imported here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ModelServiceSettings
from ..errors import BizCode
from ..schemas.invoke_schema import (
    ASRInvokeRequest,
    ImageInvokeRequest,
    InvokeRequestBody,
    LLMInvokeRequest,
    VideoInvokeRequest,
)
from .invoke_service import InvokeAttribution, InvokeFailure, InvokeOutcome

#: Bounded chunk queue: absorbs provider bursts without hiding a stalled
#: consumer (``put`` back-pressure propagates to the upstream provider).
CHUNK_QUEUE_MAXSIZE = 256

InvokeCall = Callable[[], Awaitable[InvokeOutcome]]


@dataclass(frozen=True)
class InvokePlan:
    """Zero-arg facade call plus its request-level timeout and chunk sink."""

    call: InvokeCall
    total_timeout_s: float
    chunk_sink: asyncio.Queue[dict[str, Any]] | None = None


def invoke_timeout_tiers(
    settings: ModelServiceSettings, request: InvokeRequestBody
) -> tuple[float, float]:
    """(first_result_s, total_s) by family: llm wide, media second-wide, structured short."""
    if isinstance(request, LLMInvokeRequest):
        return (
            settings.invoke_llm_first_result_timeout_s,
            settings.invoke_llm_total_timeout_s,
        )
    if isinstance(request, (ASRInvokeRequest, ImageInvokeRequest, VideoInvokeRequest)):
        return (
            settings.invoke_media_first_result_timeout_s,
            settings.invoke_media_total_timeout_s,
        )
    return settings.invoke_first_result_timeout_s, settings.invoke_total_timeout_s


def build_invoke_plan(
    invoke_call: Callable[..., Awaitable[InvokeOutcome]],
    db: AsyncSession,
    *,
    settings: ModelServiceSettings,
    tenant_id: UUID,
    request: InvokeRequestBody,
    attribution: InvokeAttribution,
    runtime: Any,
    chunk_sink: asyncio.Queue[dict[str, Any]] | None = None,
) -> InvokePlan:
    """Bind the facade call for one request (raises nothing; planning only)."""
    first_result_timeout_s, total_timeout_s = invoke_timeout_tiers(settings, request)
    if isinstance(request, LLMInvokeRequest) and request.stream:
        # Streamed llm: the first-frame budget covers parse + first chunk only;
        # draining runs outside the budget and inter-chunk idle is timed by the
        # facade. No result frame is emitted for this shape.
        sink = chunk_sink if chunk_sink is not None else asyncio.Queue(
            maxsize=CHUNK_QUEUE_MAXSIZE
        )
        call = partial(
            invoke_call,
            db,
            tenant_id=tenant_id,
            request=request,
            attribution=attribution,
            first_result_timeout_s=first_result_timeout_s,
            first_frame_timeout_s=total_timeout_s,
            idle_timeout_s=settings.invoke_llm_idle_timeout_s,
            chunk_sink=sink,
            client_pool=runtime.model_runtime.pool,
            cooldown_sessionmaker=runtime.database.async_session,
        )
        return InvokePlan(call=call, total_timeout_s=total_timeout_s, chunk_sink=sink)
    call = partial(
        invoke_call,
        db,
        tenant_id=tenant_id,
        request=request,
        attribution=attribution,
        first_result_timeout_s=first_result_timeout_s,
        client_pool=runtime.model_runtime.pool,
        cooldown_sessionmaker=runtime.database.async_session,
        media_poll_interval_s=settings.invoke_media_poll_interval_s,
        media_poll_timeout_s=settings.invoke_media_poll_timeout_s,
    )
    return InvokePlan(call=call, total_timeout_s=total_timeout_s)


async def guarded(call: InvokeCall, *, timeout_s: float) -> InvokeOutcome:
    """Request-level first-frame cap (streamed llm is excluded by the caller).

    The per-candidate upstream budget lives inside the facade; this tier is
    the bail-out that keeps the host from timing out first.
    """
    try:
        return await asyncio.wait_for(call(), timeout=timeout_s)
    except TimeoutError as exc:
        raise InvokeFailure(
            code=BizCode.SERVICE_UNAVAILABLE,
            message=f"模型调用超时（>{timeout_s:g}s 未产出结果）",
            http_status=504,
        ) from exc


__all__ = [
    "CHUNK_QUEUE_MAXSIZE",
    "InvokeCall",
    "InvokePlan",
    "build_invoke_plan",
    "guarded",
    "invoke_timeout_tiers",
]
