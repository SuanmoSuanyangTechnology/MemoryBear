"""Versioned knowledge management; registered before the legacy dynamic paths."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import WrapValidator

from ...application.runtime import ProcessRuntime
from ...errors import KnowledgeError
from ...parser_configuration.options import configuration_options
from ...rag.retrieval.async_elasticsearch import collection_name_for_knowledge
from ...repositories.reference import ReferenceRepository
from ...services import document as document_service
from ...services import document_v1 as document_v1_service
from ...services import knowledge as shared
from ...services import knowledge_v1 as service
from ..dependencies import Principal, get_principal, get_runtime, get_source, is_manager_request
from ..knowledge_capacity import check_workspace_capacity
from ..query_validation import localized_query_validation
from ..schemas.common import SuccessEnvelope, success
from ..schemas.knowledge import project_public_knowledge_data
from ..schemas.knowledge_v1 import KnowledgeCreate, KnowledgePatch, KnowledgeStatusPatch
from .knowledge import dispatch_knowledge_update

router = APIRouter(
    prefix="/knowledges/v1",
    tags=["knowledge-v1"],
    dependencies=[Depends(get_source)],
)


def knowledge_response(request: Request, data: dict[str, Any], msg: str = "OK") -> dict[str, Any]:
    if (request.headers.get("X-Source") or request.headers.get("X-KB-Source")) == "ex_api":
        if "items" in data:
            data = {
                **data,
                "items": [project_public_knowledge_data(item) for item in data["items"]],
            }
        else:
            data = project_public_knowledge_data(data)
    return success(data, msg=msg)


def check_query(request: Request, allowed: set[str]) -> None:
    if set(request.query_params) - allowed:
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            status_code=422,
            diagnostic="query: unexpected parameter",
        )


@router.get("/configuration-options", response_model=SuccessEnvelope[dict[str, Any]])
async def get_configuration_options(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    check_query(request, set())
    async with runtime.database.async_session() as db:
        workspace = await ReferenceRepository.get_workspace(db, principal.workspace_id)
        if workspace is None or workspace.tenant_id != principal.tenant_id:
            raise KnowledgeError.from_code("KB_WORKSPACE_NOT_FOUND")
        return success(configuration_options(workspace))


@router.get("/folder-tree", response_model=SuccessEnvelope[dict[str, Any]])
async def get_folder_tree(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: UUID | None = None,
) -> dict[str, Any]:
    if not is_manager_request(request):
        raise KnowledgeError.from_code("KB_ROUTE_NOT_FOUND", status_code=404)
    check_query(request, {"parent_id"})
    async with runtime.database.async_session() as db:
        return success(await service.folder_tree(db, principal, parent_id))


@router.get("", response_model=SuccessEnvelope[dict[str, Any]])
async def list_knowledges(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=100)] = 20,
    orderby: str | None = None,
    desc: bool = True,
    keywords: str | None = None,
    kb_ids: str | None = None,
    status: Annotated[
        int | None,
        Query(ge=0, le=1, description="0: disabled, 1: enabled"),
        WrapValidator(localized_query_validation),
    ] = None,
    exclude_types: str | None = None,
    is_shared: bool = False,
) -> dict[str, Any]:
    check_query(request, {
        "parent_id", "page", "pagesize", "orderby", "desc", "keywords", "kb_ids", "is_shared",
        "status", "exclude_types",
    })
    async with runtime.database.async_session() as db:
        return knowledge_response(
            request,
            await service.list_knowledges(
                db,
                principal,
                parent_id=parent_id,
                page=page,
                pagesize=pagesize,
                orderby=orderby,
                desc=desc,
                keywords=keywords,
                kb_ids=kb_ids,
                status=status,
                exclude_types=exclude_types,
                is_shared=is_shared,
            ),
        )


@router.post("", response_model=SuccessEnvelope[dict[str, Any]])
async def create_knowledge(
    request: Request,
    body: KnowledgeCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    check_query(request, set())
    await check_workspace_capacity(request, runtime, principal)
    async with runtime.database.async_session() as db:
        owner = await service.create_knowledge(
            db, body.model_dump(exclude_unset=True), principal, runtime
        )
        return knowledge_response(
            request, (await service.project_knowledges(db, [owner]))[0]
        )


@router.get("/{knowledge_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def get_knowledge(
    request: Request,
    knowledge_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    check_query(request, set())
    async with runtime.database.async_session() as db:
        owner = await service.get_knowledge(db, knowledge_id, principal)
        return knowledge_response(
            request, (await service.project_knowledges(db, [owner]))[0]
        )


@router.get("/{kb_id}/documents", response_model=SuccessEnvelope[dict[str, Any]])
async def list_documents(
    request: Request, kb_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=100)] = 20,
    orderby: str | None = None, desc: bool = False,
    keywords: str | None = None, document_ids: str | None = None,
    include_wiki_count: Annotated[bool, WrapValidator(localized_query_validation)] = False,
) -> dict[str, Any]:
    check_query(request, {
        "parent_id", "page", "pagesize", "orderby", "desc", "keywords", "document_ids",
        "include_wiki_count",
    })
    async with runtime.database.async_session() as db:
        await document_v1_service.require_knowledge(db, kb_id, principal)
        total, items = await document_service.list_documents(
            db, kb_id, principal, parent_id=parent_id, page=page, pagesize=pagesize,
            orderby=orderby, desc=desc, keywords=keywords, document_ids=document_ids,
            include_wiki_count=include_wiki_count and is_manager_request(request),
            project=document_v1_service.document_to_data,
        )
        return success({
            "items": items,
            "page": {"page": page, "pagesize": pagesize, "total": total,
                     "hasnext": page * pagesize < total},
        })


@router.patch("/{knowledge_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def patch_knowledge(
    request: Request,
    knowledge_id: UUID,
    body: KnowledgePatch | KnowledgeStatusPatch,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict[str, Any]:
    check_query(request, set())
    async with runtime.database.async_session() as db:
        if isinstance(body, KnowledgeStatusPatch):
            data = await service.update_status(db, knowledge_id, body.status, principal)
            return knowledge_response(request, data)
        plan = await service.prepare_update(
            db,
            knowledge_id,
            body.model_dump(exclude_unset=True),
            principal,
            runtime,
        )
        if plan.delete_vector_index and plan.write_input_error is None:
            await (await runtime.elasticsearch.client()).indices.delete(
                index=collection_name_for_knowledge(knowledge_id),
                ignore_unavailable=True,
            )
        outcome = await shared.apply_knowledge_update(
            db,
            plan,
            principal,
            max_folder_depth=runtime.settings.kb_knowledge_folder_max_depth,
            wiki_backfill_batch_size=runtime.settings.kb_wiki_backfill_batch_size,
        )
        owner = await service.get_knowledge(db, knowledge_id, principal)
        data = (await service.project_knowledges(db, [owner]))[0]
    message = await dispatch_knowledge_update(request, runtime, outcome)
    return knowledge_response(request, data, msg=message)
