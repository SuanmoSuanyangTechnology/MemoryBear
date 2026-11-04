"""Domain modules exposed by the profile service."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.analytics import router as analytics_router
from src.modules.health import router as health_router
from src.modules.memory_display import router as memory_display_router

router = APIRouter(prefix="/internal/v1")
router.include_router(health_router)
router.include_router(analytics_router)
router.include_router(memory_display_router)

__all__ = ["router"]
