"""Unauthenticated liveness and dependency readiness routes."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from src.modules.health.handler import HealthHandler

router = APIRouter(prefix="/health", tags=["Health"])


def _handler(request: Request) -> HealthHandler:
    return HealthHandler(request.app.state.runtime)


@router.get("/metrics")
async def metrics(
    request: Request,
    top: int = Query(0, ge=0, le=50),
) -> JSONResponse:
    return await _handler(request).metrics(top)


@router.get("/live")
async def live(request: Request) -> dict[str, str]:
    return await _handler(request).live()


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    return await _handler(request).ready()
