"""管理端 FAQ 接口：作用域隔离，内容编辑与审核/启停独立。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...repositories.wiki_faq import read, write
from ...runtime import ProcessRuntime
from ...services import wiki_faq as service
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
    request: Request,
    kb_id: UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
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
    scope = scope_for(kb_id, principal)
    pagesize = pagesize if "pagesize" in request.query_params else limit or pagesize
    return success(
        data=await service.run(
            runtime,
            read.list_faqs,
            scope,
            review_status=review_status,
            is_enabled=is_enabled,
            keyword=keyword,
            page=page,
            limit=pagesize,
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
    """审核当前 FAQ 内容并将来源基线推进至最新已发布 Wiki 快照。

    每项只需 faq_id；旧版本和快照字段兼容接收但忽略。已通过且基线最新的条目跳过，
    保留逐条处理结果。不会自动改写问答或启用 FAQ；单条审核仍校验客户端审核目标。
    """
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


@router.get("/{faq_id}", response_model=SuccessEnvelope[dict])
async def get_faq(
    kb_id: UUID,
    faq_id: UUID,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    scope = scope_for(kb_id, principal)
    return success(data=await service.run(runtime, read.detail, scope, faq_id))


@router.patch("/{faq_id}", response_model=SuccessEnvelope[dict])
async def edit_faq(
    kb_id: UUID,
    faq_id: UUID,
    payload: FAQEdit,
    principal: PrincipalDep,
    runtime: RuntimeDep,
):
    """保存问答；已变更 FAQ 同时确认当前 Wiki 基线并恢复为已通过。

    来源不可用或问答原文未改变也可确认基线。待审核 FAQ 保持待审核，启用状态不变；
    仍须提交 expected_row_version，保留原并发校验。缺少当前版本快照时整次保存失败。
    """
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
    principal: PrincipalDep,
    runtime: RuntimeDep,
    expected_row_version: int = Query(..., ge=1, description="FAQ 当前行版本，删除前校验并发"),
):
    scope = scope_for(kb_id, principal)
    return success(
        data=await service.run(
            runtime,
            write.delete,
            scope,
            faq_id,
            principal.actor_id,
            {"expected_row_version": expected_row_version},
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
