"""Internal Document routes migrated from the legacy controller."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request

from ...runtime import ProcessRuntime
from ...services import document as document_service
from ...services import file as file_service
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.knowledge_metadata import KnowledgeMetadataService
from ...tasks.dispatch import TaskDispatcher
from ..dependencies import Principal, get_principal, get_runtime
from ..schemas.common import SuccessEnvelope
from ..schemas.document import DocumentCreate, DocumentUpdate
from ..schemas.knowledge_metadata import (
    BatchUpdateMetadataRequest,
    DocumentMetadataDeleteRequest,
    DocumentMetadataUpdateRequest,
)

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(get_principal)],
)


def _success(request: Request, data: Any = None) -> SuccessEnvelope[Any]:
    return SuccessEnvelope(data=data, trace_id=request.state.trace_id)


async def _require_document(
    db: Any,
    document_id: uuid.UUID,
    principal: Principal,
) -> None:
    if await document_service.get_document(db, document_id, principal) is None:
        raise document_service._not_found()


@router.get("/{kb_id}/documents", response_model=SuccessEnvelope[dict[str, Any]])
async def get_documents(
    request: Request,
    kb_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: Annotated[uuid.UUID | None, Query()] = None,
    page: Annotated[int, Query(gt=0)] = 1,
    pagesize: Annotated[int, Query(gt=0, le=100)] = 20,
    orderby: Annotated[str | None, Query()] = None,
    desc: Annotated[bool, Query()] = False,
    keywords: Annotated[str | None, Query()] = None,
    document_ids: Annotated[str | None, Query()] = None,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        total, items = await document_service.list_documents(
            db,
            kb_id,
            principal,
            parent_id=parent_id,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            keywords=keywords,
            document_ids=document_ids,
        )
    return _success(
        request,
        {
            "items": items,
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "has_next": page * pagesize < total,
            },
        },
    )


@router.post("/document", response_model=SuccessEnvelope[dict[str, Any]])
async def create_document(
    request: Request,
    create_data: DocumentCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        document = await document_service.create_document(db, create_data, principal)
    return _success(request, document_service.document_to_data(document))


@router.get("/{document_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def get_document(
    request: Request,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        document = await document_service.get_document(db, document_id, principal)
        if document is None:
            raise document_service._not_found()
        data = document_service.document_to_data(document)
    return _success(request, data)


@router.put("/{document_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def update_document(
    request: Request,
    document_id: uuid.UUID,
    update_data: DocumentUpdate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        plan = await document_service.prepare_document_update(
            db,
            document_id,
            update_data,
            principal,
        )
    if plan.status_changed:
        await document_service.change_document_status(
            await runtime.elasticsearch.client(),
            plan.knowledge_id,
            plan.document_id,
            plan.update_fields["status"],
        )
    async with runtime.database.async_session() as db:
        document = await document_service.apply_document_update(db, plan, principal)
        data = document_service.document_to_data(document)
    if plan.status_changed:
        await document_service.dispatch_document_graph_sync(
            TaskDispatcher(),
            plan.knowledge_id,
            plan.document_id,
            plan.graph_parser_config,
            dispatch_legacy=False,
        )
    return _success(request, data)


@router.delete("/{document_id}", response_model=SuccessEnvelope[None])
async def delete_document(
    request: Request,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[None]:
    async with runtime.database.async_session() as db:
        snapshot = await document_service.prepare_document_deletion(
            db,
            document_id,
            principal,
        )
    redis = await runtime.redis.client()
    search = await runtime.elasticsearch.client()

    async def delete_search() -> None:
        await document_service.delete_document_search_data(
            search,
            snapshot.knowledge_id,
            snapshot.document_id,
        )

    async def delete_records() -> None:
        async with runtime.database.async_session() as db:
            await document_service.delete_document_records(db, snapshot)

    await document_service.delete_document_resources(
        snapshot,
        redis=redis,
        dispatcher=TaskDispatcher(),
        storage=KnowledgeFileStorage(runtime.storage),
        delete_search=delete_search,
        delete_records=delete_records,
    )
    return _success(request)


@router.post("/{document_id}/chunks", response_model=SuccessEnvelope[dict[str, Any]])
async def parse_documents(
    request: Request,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        document = await document_service.get_document(db, document_id, principal)
        if document is None:
            raise document_service._not_found()
        file = await file_service.get_file(db, document.file_id, principal, document.kb_id)
        if file is None:
            raise file_service._not_found()
        if not file.file_key:
            raise file_service._not_found("File has no storage key (legacy data not migrated)")
        snapshot = document_service.ParseDocumentSnapshot(
            document_id=document.id,
            file_key=file.file_key,
            file_name=file.file_name,
        )
    result = await document_service.claim_and_dispatch_parse(
        await runtime.redis.client(),
        TaskDispatcher(),
        snapshot,
    )
    return _success(request, {"task_id": result.task_id})


@router.post("/metadata/batch", response_model=SuccessEnvelope[dict[str, Any]])
async def batch_update_document_metadata(
    request: Request,
    data: BatchUpdateMetadataRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        for item in data.items:
            await _require_document(db, item.document_id, principal)
        result = await KnowledgeMetadataService.batch_update_document_metadata_async(
            db,
            [
                {"document_id": item.document_id, "metadata": item.metadata}
                for item in data.items
            ],
            principal.tenant_id,
            principal.actor_id,
        )
    return _success(request, result)


@router.put("/{document_id}/metadata", response_model=SuccessEnvelope[dict[str, Any]])
async def update_document_metadata(
    request: Request,
    document_id: uuid.UUID,
    data: DocumentMetadataUpdateRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_document(db, document_id, principal)
        result = await KnowledgeMetadataService.update_document_metadata_async(
            db,
            document_id,
            data.metadata,
            principal.tenant_id,
            principal.actor_id,
        )
    return _success(request, result)


@router.get("/{document_id}/metadata", response_model=SuccessEnvelope[dict[str, Any]])
async def get_document_metadata(
    request: Request,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_document(db, document_id, principal)
        result = await KnowledgeMetadataService.get_document_metadata_async(db, document_id)
    return _success(request, result)


@router.post("/{document_id}/metadata", response_model=SuccessEnvelope[dict[str, Any]])
async def delete_document_metadata(
    request: Request,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    data: Annotated[DocumentMetadataDeleteRequest | None, Body()] = None,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        await _require_document(db, document_id, principal)
        result = await KnowledgeMetadataService.delete_document_metadata_async(
            db,
            document_id,
            data.field_names if data else None,
        )
    return _success(request, result)


__all__ = ["router"]
