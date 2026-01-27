from fastapi import APIRouter

from . import health_controller, sandbox_controller

manager_router = APIRouter()

manager_router.include_router(health_controller.router)
manager_router.include_router(sandbox_controller.router)
