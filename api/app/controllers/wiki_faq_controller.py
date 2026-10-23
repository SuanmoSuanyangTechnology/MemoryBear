"""FAQ 管理的 JWT/workspace 鉴权与转发，业务仅在 mem-knowledge 实现。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.integrations.knowledge.contracts import KnowledgeRetrievalSource
from app.integrations.knowledge.route_proxy import route_through_knowledge_service
from app.models.user_model import User
from app.schemas.wiki_faq_schema import (
    FAQApprove,
    FAQBatchApprove,
    FAQBatchContext,
    FAQBatchEnabled,
    FAQCreate,
    FAQEdit,
    FAQEnabled,
)

router = APIRouter(
    prefix="/knowledges/{kb_id}/faqs",
    tags=["wiki-faq"],
    dependencies=[Depends(get_current_user_async)],
)


@router.get("")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def list_faqs(
    request: Request,
    kb_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
    review_status: Literal["pending", "approved", "change_pending"] | None = None,
    is_enabled: bool | None = None,
    keyword: str | None = Query(
        None,
        description="搜索标准问和标准答（answers[0]），不区分大小写的包含匹配；"
        "忽略首尾空白，空白不过滤，% 和 _ 按普通字符匹配。",
    ),
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
    limit: int | None = Query(None, ge=1, le=100, include_in_schema=False),
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.post("")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def create_faq(
    request: Request,
    kb_id: UUID,
    payload: FAQCreate,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.post("/batch/review-context")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def batch_review_context(
    request: Request,
    kb_id: UUID,
    payload: FAQBatchContext,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.post("/batch/approve")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def batch_approve(
    request: Request,
    kb_id: UUID,
    payload: FAQBatchApprove,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.patch("/batch/enabled")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def batch_enabled(
    request: Request,
    kb_id: UUID,
    payload: FAQBatchEnabled,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.get("/{faq_id}")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_faq(
    request: Request,
    kb_id: UUID,
    faq_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.patch("/{faq_id}")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def edit_faq(
    request: Request,
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQEdit,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.patch("/{faq_id}/enabled")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def enable_faq(
    request: Request,
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQEnabled,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.delete("/{faq_id}")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def delete_faq(
    request: Request,
    kb_id: UUID,
    faq_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
    expected_row_version: int = Query(..., ge=1, description="FAQ 当前行版本，删除前校验并发"),
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.get("/{faq_id}/review-context")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def get_review_context(
    request: Request,
    kb_id: UUID,
    faq_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")


@router.post("/{faq_id}/approve")
@cur_workspace_access_guard_async()
@route_through_knowledge_service(source=KnowledgeRetrievalSource.MANAGER_API)
async def approve_faq(
    request: Request,
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQApprove,
    current_user: Annotated[User, Depends(get_current_user_async)],
):
    raise HTTPException(status_code=503, detail="FAQ requires mem-knowledge")
