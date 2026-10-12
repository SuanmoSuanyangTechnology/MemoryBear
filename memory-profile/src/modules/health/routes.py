"""Unauthenticated liveness and dependency readiness routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.modules.health.handler import HealthHandler

router = APIRouter(prefix="/health", tags=["Health"])


def _handler(request: Request) -> HealthHandler:
    return HealthHandler(request.app.state.runtime)


@router.get("/live")
async def live(request: Request) -> dict[str, str]:
    return await _handler(request).live()


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    return await _handler(request).ready()
