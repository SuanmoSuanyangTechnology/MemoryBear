"""Internal API router composition."""

from fastapi import APIRouter

from .routes.chunk import router as chunk_router
from .routes.document import router as document_router
from .routes.file import router as file_router
from .routes.health import router as health_router
from .routes.knowledge import router as knowledge_router
from .routes.knowledge_metadata import router as knowledge_metadata_router
from .routes.knowledge_share import router as knowledge_share_router

internal_v1_router = APIRouter(prefix="/internal/v1")
internal_v1_router.include_router(health_router)
internal_v1_router.include_router(chunk_router)
internal_v1_router.include_router(document_router)
internal_v1_router.include_router(file_router)
internal_v1_router.include_router(knowledge_router)
internal_v1_router.include_router(knowledge_metadata_router)
internal_v1_router.include_router(knowledge_share_router)

__all__ = ["internal_v1_router"]
