"""Internal API router composition."""

from fastapi import APIRouter

from .routes.apps import router as apps_router
from .routes.health import router as health_router
from .routes.multi_agent import router as multi_agent_router
from .routes.skills import router as skills_router
from .routes.tools import router as tools_router

internal_v1_router = APIRouter(prefix="/internal/v1")
internal_v1_router.include_router(health_router)
internal_v1_router.include_router(apps_router)
internal_v1_router.include_router(multi_agent_router)
internal_v1_router.include_router(skills_router)
internal_v1_router.include_router(tools_router)

__all__ = ["internal_v1_router"]
