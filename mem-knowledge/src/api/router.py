"""Internal API router composition."""

from fastapi import APIRouter

from .routes.health import router as health_router
from .routes.knowledge import router as knowledge_router
from .routes.knowledge_share import router as knowledge_share_router

internal_v1_router = APIRouter(prefix="/internal/v1")
internal_v1_router.include_router(health_router)
internal_v1_router.include_router(knowledge_router)
internal_v1_router.include_router(knowledge_share_router)

__all__ = ["internal_v1_router"]
