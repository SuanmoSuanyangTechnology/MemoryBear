"""Liveness and dependency readiness routes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...runtime import ProcessRuntime
from ..schemas.health import ComponentHealth, HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])


def _timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


async def _probe_component(
    probe: Callable[[], Awaitable[bool]],
    timeout_seconds: float,
) -> ComponentHealth:
    started = time.perf_counter_ns()
    try:
        async with asyncio.timeout(timeout_seconds):
            available = await probe()
        status = "up" if available else "down"
        error_type = None if available else "Unavailable"
    except TimeoutError:
        status = "timeout"
        error_type = "TimeoutError"
    except Exception as exc:
        status = "down"
        error_type = type(exc).__name__
    latency_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
    return ComponentHealth(
        status=status,
        latency_ms=latency_ms,
        error_type=error_type,
    )


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    runtime: ProcessRuntime = request.app.state.runtime
    return HealthResponse(
        status="alive",
        process_role=runtime.settings.process_role,
        checked_at_ms=_timestamp_ms(),
        trace_id=request.state.trace_id,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse | JSONResponse:
    runtime: ProcessRuntime = request.app.state.runtime
    timeout_seconds = runtime.settings.health_probe_timeout_seconds
    names = ("database", "redis", "elasticsearch", "storage")
    probes = (
        runtime.database.ping,
        runtime.redis.ping,
        runtime.elasticsearch.ping,
        runtime.storage.ping,
    )
    results = await asyncio.gather(
        *(_probe_component(probe, timeout_seconds) for probe in probes)
    )
    components = dict(zip(names, results, strict=True))
    is_ready = all(component.status == "up" for component in results)
    response = HealthResponse(
        status="ready" if is_ready else "not_ready",
        process_role=runtime.settings.process_role,
        checked_at_ms=_timestamp_ms(),
        trace_id=request.state.trace_id,
        code=None if is_ready else "KNOWLEDGE_NOT_READY",
        retryable=None if is_ready else True,
        components=components,
    )
    if is_ready:
        return response
    return JSONResponse(
        status_code=503,
        content=response.model_dump(mode="json", exclude_none=True),
    )
