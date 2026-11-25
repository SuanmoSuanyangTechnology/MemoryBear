from fastapi import APIRouter

from .routes import health, internal_files, metrics, storage

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(metrics.router)
api_router.include_router(storage.router)
api_router.include_router(internal_files.router)
