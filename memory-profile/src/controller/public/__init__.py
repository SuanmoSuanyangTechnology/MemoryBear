from fastapi import APIRouter

from src.controller.public import health

public_router = APIRouter()

public_router.include_router(health.router)

__all__ = ["public_router"]