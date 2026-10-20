"""管理端 FAQ 接口：作用域隔离，内容编辑与审核/启停独立。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ...repositories.wiki_faq import read, write
from ...runtime import ProcessRuntime
from ...services import wiki_faq as service
from ...services.wiki_faq_retrieval import index_state
from ..dependencies import Principal, get_principal, get_runtime, get_source
from ..schemas.chunk import KnowledgeRetrievalSource
from ..schemas.common import SuccessEnvelope, success
from ..schemas.wiki_faq import (
    FAQApprove,
    FAQBatchApprove,
    FAQBatchContext,
    FAQBatchEnabled,
    FAQCreate,
    FAQEdit,
    FAQEnabled,
    FAQExpected,
)


def manager_principal(
    principal: Annotated[Principal, Depends(get_principal)],
    source: Annotated[KnowledgeRetrievalSource, Depends(get_source)],
) -> Principal:
    if source != KnowledgeRetrievalSource.MANAGER_API:
        raise HTTPException(403, "FAQ management requires a management principal")
    return principal


router = APIRouter(prefix="/knowledges/{kb_id}/faqs", tags=["wiki-faq"])
PrincipalDep = Annotated[Principal, Depends(manager_principal)]
RuntimeDep = Annotated[ProcessRuntime, Depends(get_runtime)]


def scope_for(kb_id, principal):
    return {
        "kb_id": kb_id,
        "tenant_id": principal.tenant_id,
        "workspace_id": principal.workspace_id,
    }


@router.get("", response_model=SuccessEnvelope[dict])
async def list_faqs(
    kb_id: UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    review_status: Literal["pending", "approved", "change_pending"] | None = None,
    is_enabled: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime,
            read.list_faqs,
            scope,
            review_status=review_status,
            is_enabled=is_enabled,
            page=page,
            limit=limit,
        )
    )


@router.post("", response_model=SuccessEnvelope[dict])
async def create_faq(
    kb_id: UUID,
    payload: FAQCreate,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime, write.create, scope, principal.actor_id, payload.model_dump()
        )
    )


@router.post("/batch/review-context", response_model=SuccessEnvelope[dict])
async def batch_review_context(
    kb_id: UUID,
    payload: FAQBatchContext,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(data=await service.run(runtime, read.batch_context, scope, payload.faq_ids))


@router.post("/batch/approve", response_model=SuccessEnvelope[dict])
async def batch_approve(
    kb_id: UUID,
    payload: FAQBatchApprove,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.batch(
            runtime,
            scope,
            principal.actor_id,
            [item.model_dump() for item in payload.items],
            action="approve",
        )
    )


@router.patch("/batch/enabled", response_model=SuccessEnvelope[dict])
async def batch_enabled(
    kb_id: UUID,
    payload: FAQBatchEnabled,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.batch(
            runtime,
            scope,
            principal.actor_id,
            [item.model_dump() for item in payload.items],
            action="enabled",
            is_enabled=payload.is_enabled,
        )
    )


@router.get("/generation-jobs", response_model=SuccessEnvelope[dict])
async def list_generation_jobs(
    kb_id: UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
    wiki_page_id: UUID | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime, read.list_jobs, scope, wiki_page_id=wiki_page_id, page=page, limit=limit
        )
    )


@router.post("/generation-jobs/{job_id}/retry", response_model=SuccessEnvelope[dict])
async def retry_generation(
    kb_id: UUID,
    job_id: UUID,
    payload: FAQExpected,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.retry_job(runtime, scope, job_id, payload.expected_row_version)
    )


@router.get("/{faq_id}", response_model=SuccessEnvelope[dict])
async def get_faq(
    kb_id: UUID,
    faq_id: UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(data=await service.run(runtime, read.detail, scope, faq_id))


@router.get("/{faq_id}/index-status", response_model=SuccessEnvelope[dict])
async def get_index_status(kb_id: UUID, faq_id: UUID, principal: PrincipalDep, runtime: RuntimeDep):
    return success(data=await index_state(runtime, scope_for(kb_id, principal), faq_id))


@router.post("/{faq_id}/index/retry", response_model=SuccessEnvelope[dict])
async def retry_index(
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQExpected,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    return success(
        data=await index_state(
            runtime,
            scope_for(kb_id, principal),
            faq_id,
            synchronize=True,
            expected_row_version=payload.expected_row_version,
        )
    )


@router.patch("/{faq_id}", response_model=SuccessEnvelope[dict])
async def edit_faq(
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQEdit,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime, write.edit, scope, faq_id, principal.actor_id, payload.model_dump()
        )
    )


@router.patch("/{faq_id}/enabled", response_model=SuccessEnvelope[dict])
async def enable_faq(
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQEnabled,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime, write.set_enabled, scope, faq_id, principal.actor_id, payload.model_dump()
        )
    )


@router.delete("/{faq_id}", response_model=SuccessEnvelope[dict])
async def delete_faq(
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQExpected,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime, write.delete, scope, faq_id, principal.actor_id, payload.model_dump()
        )
    )


@router.get("/{faq_id}/review-context", response_model=SuccessEnvelope[dict])
async def get_review_context(
    kb_id: UUID,
    faq_id: UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(data=await service.run(runtime, read.review_context, scope, faq_id))


@router.post("/{faq_id}/approve", response_model=SuccessEnvelope[dict])
async def approve_faq(
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQApprove,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime, write.approve, scope, faq_id, principal.actor_id, payload.model_dump()
        )
    )
