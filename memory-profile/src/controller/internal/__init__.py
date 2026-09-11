from fastapi import APIRouter
from src.controller.internal.analytics_controller import router as analytics_router

internal_router = APIRouter()
internal_router.include_router(analytics_router)
__all__ = ["internal_router"]
