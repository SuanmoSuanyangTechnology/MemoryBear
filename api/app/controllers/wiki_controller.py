"""Management-only forwarding to the knowledge Wiki service."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.models.user_model import User

router = APIRouter(
    prefix="/knowledges/{kb_id}/wiki",
    tags=["wiki"],
    dependencies=[Depends(get_current_user_async)],
)


def _requires_service():
    raise HTTPException(status_code=503, detail="Wiki requires mem-knowledge")


@router.get("/tree")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def tree(
    request: Request,
    kb_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
    folder_id: uuid.UUID | None = None,
    page_types: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[
        str | None, Query(pattern=r"^[01]:[0-9a-fA-F-]{36}$", include_in_schema=False)
    ] = None,
    limit: Annotated[int | None, Query(ge=1, le=200, include_in_schema=False)] = None,
):
    return _requires_service()


@router.get("/index")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def index(
    request: Request,
    kb_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    return _requires_service()


@router.get("/pages")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def pages(
    request: Request,
    kb_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
    page_types: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=200)] = 50,
    limit: Annotated[int | None, Query(ge=1, le=200, include_in_schema=False)] = None,
):
    return _requires_service()


@router.get("/pages/{page_id}")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def page_detail(
    request: Request,
    kb_id: uuid.UUID,
    page_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    return _requires_service()


@router.get("/graph")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def graph(
    request: Request,
    kb_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
    mode: Literal["overview", "ego"] = "overview",
    center_page_id: uuid.UUID | None = None,
    types: str | None = None,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
):
    return _requires_service()


@router.post("/documents/{document_id}/generate", status_code=202)
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def generate(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    return _requires_service()
