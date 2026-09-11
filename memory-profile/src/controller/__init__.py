from fastapi import APIRouter

from src.controller.external import external_router
from src.controller.internal import internal_router
from src.controller.public import public_router

controller_router = APIRouter()

controller_router.include_router(public_router)
controller_router.include_router(internal_router, prefix='/api')
controller_router.include_router(external_router, prefix='/v1')
