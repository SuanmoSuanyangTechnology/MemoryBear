"""Liveness and dependency readiness routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...request_logging import log_request_failure
from ...runtime import ProcessRuntime
from ..schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])


def _timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


@router.get("/live", response_model=HealthResponse)
async def live(request: Request) -> HealthResponse:
    return HealthResponse(
        status="alive",
        checked_at_ms=_timestamp_ms(),
        trace_id=request.state.trace_id,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse | JSONResponse:
    runtime: ProcessRuntime = request.app.state.runtime
    readiness = runtime.readiness
    components = readiness.components
    is_ready = readiness.is_ready
    response = HealthResponse(
        status="ready" if is_ready else "not_ready",
        checked_at_ms=readiness.checked_at_ms or _timestamp_ms(),
        trace_id=request.state.trace_id,
        code=None if is_ready else "APP_NOT_READY",
        retryable=None if is_ready else True,
        components=components,
    )
    if is_ready:
        return response
    log_request_failure(
        request,
        status_code=503,
        response_code=None,
        error_code="APP_NOT_READY",
        message="Application service dependencies are not ready",
        params={
            "components": {
                name: component.model_dump(exclude_none=True)
                for name, component in components.items()
            }
        },
        retryable=True,
    )
    return JSONResponse(
        status_code=503,
        content=response.model_dump(mode="json", exclude_none=True),
    )
