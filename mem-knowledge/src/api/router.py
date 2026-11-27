"""Internal API router composition."""

from error_core.knowledge_upload import INTERNAL_UPLOAD_PREFIX
from fastapi import APIRouter

from .routes import upload_v2
from .routes.chunk import router as chunk_router
from .routes.document import download_router as document_download_router
from .routes.document import router as document_router
from .routes.document_v1 import router as document_v1_router
from .routes.faq_import import router as faq_import_router
from .routes.file import router as file_router
from .routes.file_v1 import router as file_v1_router
from .routes.health import router as health_router
from .routes.knowledge import router as knowledge_router
from .routes.knowledge_metadata import router as knowledge_metadata_router
from .routes.knowledge_share import router as knowledge_share_router
from .routes.knowledge_v1 import router as knowledge_v1_router
from .routes.wiki import router as wiki_router
from .routes.wiki_faq import router as wiki_faq_router
from .upload_contract import GATEWAY_UPLOAD_PREFIX

internal_v1_router = APIRouter(prefix=GATEWAY_UPLOAD_PREFIX)
internal_v1_router.include_router(health_router)
# 新版 QA 路径要先于旧的 /chunks/{kb_id}/{document_id}/import_qa 匹配。
internal_v1_router.include_router(upload_v2.gateway_router)
internal_v1_router.include_router(chunk_router)
internal_v1_router.include_router(document_v1_router)
internal_v1_router.include_router(document_router)
internal_v1_router.include_router(file_v1_router)
internal_v1_router.include_router(document_download_router)
internal_v1_router.include_router(file_router)
internal_v1_router.include_router(knowledge_v1_router)
internal_v1_router.include_router(knowledge_router)
internal_v1_router.include_router(knowledge_metadata_router)
internal_v1_router.include_router(knowledge_share_router)
internal_v1_router.include_router(wiki_router)

internal_v1_router.include_router(faq_import_router)
internal_v1_router.include_router(wiki_faq_router)

internal_upload_router = APIRouter(prefix=INTERNAL_UPLOAD_PREFIX)
internal_upload_router.include_router(upload_v2.router)

__all__ = ["internal_upload_router", "internal_v1_router"]
