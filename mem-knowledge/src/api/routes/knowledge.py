"""Internal Knowledge routes migrated from the legacy controller."""

from __future__ import annotations

import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from ...models.owned import FILE_ROLE_SOURCE, File, KnowledgeType, ParserType, PermissionType
from ...runtime import ProcessRuntime
from ...services import file as file_service
from ...services import knowledge as knowledge_service
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.qa_export import (
    cleanup_export_file,
    iter_export_file,
    make_qa_export_filename,
    write_document_export,
    write_knowledge_csv,
)
from ..dependencies import Principal, get_principal, get_runtime
from ..schemas.common import SuccessEnvelope
from ..schemas.file import KBBatchDownloadRequest
from ..schemas.knowledge import KnowledgeCreate, KnowledgeUpdate

router = APIRouter(
    prefix="/knowledges",
    tags=["knowledges"],
    dependencies=[Depends(get_principal)],
)


def _success(request: Request, data: Any = None) -> SuccessEnvelope[Any]:
    return SuccessEnvelope(data=data, trace_id=request.state.trace_id)


@router.get("/knowledgetype", response_model=SuccessEnvelope[list[str]])
async def get_knowledge_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(request, list(KnowledgeType))


@router.get("/permissiontype", response_model=SuccessEnvelope[list[str]])
async def get_permission_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(request, list(PermissionType))


@router.get("/parsertype", response_model=SuccessEnvelope[list[str]])
async def get_parser_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(request, list(ParserType))


@router.get("/knowledges", response_model=SuccessEnvelope[dict[str, Any]])
async def get_knowledges(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: Annotated[uuid.UUID | None, Query(description="parent folder id")] = None,
    page: Annotated[int, Query(gt=0)] = 1,
    pagesize: Annotated[int, Query(gt=0, le=100)] = 20,
    orderby: Annotated[str | None, Query()] = None,
    desc: Annotated[bool, Query()] = False,
    keywords: Annotated[str | None, Query()] = None,
    kb_ids: Annotated[str | None, Query()] = None,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        total, items = await knowledge_service.list_knowledges(
            db,
            principal,
            parent_id=parent_id,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            keywords=keywords,
            kb_ids=kb_ids,
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


@router.post("/knowledge", response_model=SuccessEnvelope[dict[str, Any]])
async def create_knowledge(
    request: Request,
    create_data: KnowledgeCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.create_knowledge(db, create_data, principal)
        data = await knowledge_service.knowledge_to_data(db, knowledge)
    return _success(request, data)


@router.get("/{knowledge_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def get_knowledge(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
        data = await knowledge_service.build_knowledge_detail_data(db, knowledge)
    return _success(request, data)


@router.get(
    "/{knowledge_id}/chunk-policy",
    response_model=SuccessEnvelope[dict[str, bool | None]],
)
async def get_knowledge_chunk_policy(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, bool | None]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
    return _success(
        request,
        {"parent_child_mode": {0: None, 1: False, 2: True}[knowledge.chunk_mode]},
    )


@router.put("/{knowledge_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def update_knowledge(
    request: Request,
    knowledge_id: uuid.UUID,
    update_data: KnowledgeUpdate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.update_knowledge(
            db,
            knowledge_id,
            update_data,
            principal,
            runtime.redis,
        )
        data = await knowledge_service.knowledge_to_data(db, knowledge)
    return _success(request, data)


@router.delete("/{knowledge_id}", response_model=SuccessEnvelope[None])
async def delete_knowledge(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[None]:
    async with runtime.database.async_session() as db:
        await knowledge_service.soft_delete_knowledge(
            db,
            knowledge_id,
            principal,
            runtime.redis,
        )
    return _success(request)


@router.get("/{kb_id}/qa/export")
async def export_knowledge_qa_csv(
    kb_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> StreamingResponse:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, kb_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
        filename = make_qa_export_filename(knowledge.name)
    path = await write_knowledge_csv(await runtime.elasticsearch.client(), kb_id)
    return StreamingResponse(
        iter_export_file(path),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        background=BackgroundTask(cleanup_export_file, path),
    )


@router.post("/{kb_id}/batch-download")
async def kb_batch_download(
    kb_id: uuid.UUID,
    request_body: KBBatchDownloadRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> StreamingResponse:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, kb_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
        result = await db.execute(
            select(File).where(
                File.kb_id == kb_id,
                File.file_role == FILE_ROLE_SOURCE,
                File.file_key.is_not(None),
                File.file_key != "",
            )
        )
        files = list(result.scalars().all())
        if not files:
            raise file_service._not_found("Knowledge has no downloadable files")
        specs = [await file_service.get_qa_export_spec(db, file) for file in files]
        knowledge_name = knowledge.name
    client = await runtime.elasticsearch.client()
    qa_exports = {}
    for file, spec in zip(files, specs, strict=True):
        if spec is None:
            continue
        result = await write_document_export(
            client,
            spec.kb_id,
            spec.document_id,
            spec.file_ext,
        )
        if result:
            path, media_type = result
            qa_exports[file.file_key] = file_service.QAExportFile(
                path,
                spec.file_name,
                media_type,
            )
    entries = file_service.build_zip_arcnames(files)
    zip_name = file_service.make_zip_filename(
        files,
        request_body.zip_filename,
        base_name=knowledge_name,
    )
    return StreamingResponse(
        file_service.stream_zip_files(
            entries,
            KnowledgeFileStorage(runtime.storage),
            qa_exports,
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}",
            "X-Total-Files": str(len(files)),
        },
    )
