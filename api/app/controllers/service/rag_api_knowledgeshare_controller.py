"""Knowledge sharing through the existing workspace-scoped RAG API Key."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.response_utils import success
from app.db import get_async_db
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.schemas.knowledgeshare_schema import (
    KnowledgeShareRequest,
    KnowledgeShareResult,
)
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service, knowledgeshare_service
from app.services.rag_access_service import get_api_key_request_user

router = APIRouter(prefix="/knowledgeshares", tags=["V1 - RAG API"])


@router.post("/share", response_model=ApiResponse)
@require_api_key_self_db(scopes=["rag"])
@route_through_knowledge_service(source=KnowledgeRetrievalSource.EXTERNAL_API)
async def create_knowledgeshare(
    request: Request,
    create_data: KnowledgeShareRequest,
    db: Annotated[AsyncSession, Depends(get_async_db)],
):
    """Create or reuse a same-tenant share; configure Agents with target_kb_id."""
    api_key_auth = get_current_api_key_auth()
    api_key = await api_key_service.ApiKeyService.get_api_key_async(
        db,
        api_key_auth.api_key_id,
        api_key_auth.workspace_id,
    )
    current_user = get_api_key_request_user(api_key, api_key_auth)
    share, created = await knowledgeshare_service.create_or_reuse_knowledgeshare_async(
        db,
        create_data,
        current_user,
    )
    result = KnowledgeShareResult.model_validate(share).model_copy(
        update={"created": created}
    )
    return success(data=result.model_dump(mode="json"))
