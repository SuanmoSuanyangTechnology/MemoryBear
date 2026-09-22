"""Internal API router composition."""

from fastapi import APIRouter

from .routes.health import router as health_router
from .routes.models import router as models_router
from .routes.platform_channels import router as platform_channels_router
from .routes.platform_models import router as platform_models_router

internal_v1_router = APIRouter(prefix="/internal/v1")
internal_v1_router.include_router(health_router)
internal_v1_router.include_router(models_router)
internal_v1_router.include_router(platform_models_router)
internal_v1_router.include_router(platform_channels_router)

__all__ = ["internal_v1_router"]
