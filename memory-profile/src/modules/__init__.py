"""Domain modules exposed by the profile service."""

from __future__ import annotations

from fastapi import APIRouter

from src.modules.analytics import router as analytics_router
from src.modules.display import router as display_router
from src.modules.health import router as health_router
from src.modules.implicit_memory import router as implicit_memory_router
from src.modules.memory_config import (
    community_router as memory_config_community_router,
)
from src.modules.memory_config import router as memory_config_router
from src.modules.perceptual import router as perceptual_router
from src.modules.short import router as short_router
from src.modules.value_ranking import router as value_ranking_router

router = APIRouter(prefix="/internal/v1")
router.include_router(health_router)
router.include_router(analytics_router)
router.include_router(memory_config_router)
router.include_router(memory_config_community_router)
router.include_router(display_router)
router.include_router(perceptual_router)
router.include_router(implicit_memory_router)
router.include_router(short_router)
router.include_router(value_ranking_router)

__all__ = ["router"]
