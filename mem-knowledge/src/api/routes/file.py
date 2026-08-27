"""Internal File routes migrated from the legacy controller."""

from __future__ import annotations

import mimetypes
import os
import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi import File as UploadBody
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from ...errors import KnowledgeError
from ...models.owned import FILE_ROLE_SOURCE, File, Knowledge
from ...runtime import ProcessRuntime
from ...services import file as file_service
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.qa_export import cleanup_export_file, iter_export_file, write_document_export
from ..dependencies import (
    Principal,
    get_optional_principal,
    get_principal,
    get_runtime,
    get_source,
)
from ..schemas.chunk import KnowledgeRetrievalSource
from ..schemas.common import SuccessEnvelope, success
from ..schemas.file import (
    BatchDownloadRequest,
    CustomTextFileCreate,
    FileUpdate,
)

router = APIRouter(prefix="/files", tags=["files"])


def _success(
    _request: Request,
    data: Any = None,
    msg: str = "OK",
) -> dict[str, Any]:
    return success(data=data, msg=msg)


async def _qa_export(
    runtime: ProcessRuntime,
    spec: file_service.QAExportSpec,
) -> file_service.QAExportFile | None:
    result = await write_document_export(
        await runtime.elasticsearch.client(),
        spec.kb_id,
        spec.document_id,
        spec.file_ext,
    )
    if result is None:
        return None
    path, media_type = result
    return file_service.QAExportFile(path, spec.file_name, media_type)


async def _persist_upload(
    runtime: ProcessRuntime,
    principal: Principal,
    *,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    file_name: str,
    file_ext: str,
    content: bytes,
    content_type: str | None,
    inherit_parser_config: bool,
) -> file_service.UploadOutcome:
    storage = KnowledgeFileStorage(runtime.storage)
    async with runtime.database.async_session() as db:
        plan = await file_service.upload_content(
            db,
            storage,
            kb_id=kb_id,
            parent_id=parent_id,
            file_name=file_name,
            file_ext=file_ext,
            content=content,
            content_type=content_type,
            principal=principal,
            inherit_parser_config=inherit_parser_config,
        )
    await storage.upload(plan.file_key, content, content_type)
    persistence_succeeded = False
    try:
        async with runtime.database.async_session() as db:
            outcome = await file_service.persist_uploaded_content(db, plan, principal)
            persistence_succeeded = True
    except Exception:
        if not persistence_succeeded:
            await file_service.compensate_storage_upload(storage, plan.file_key)
        raise
    return outcome


@router.get("/{kb_id}/{parent_id}/files", response_model=SuccessEnvelope[dict[str, Any]])
async def get_files(
    request: Request,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    page: Annotated[int, Query(gt=0)] = 1,
    pagesize: Annotated[int, Query(gt=0, le=100)] = 20,
    orderby: Annotated[str | None, Query()] = None,
    desc: Annotated[bool, Query()] = False,
    keywords: Annotated[str | None, Query()] = None,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        total, items = await file_service.list_files(
            db,
            kb_id,
            parent_id,
            principal,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            keywords=keywords,
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
        "Query of file list succeeded",
    )


@router.post("/folder", response_model=SuccessEnvelope[dict[str, Any]])
async def create_folder(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    folder_name: str = "/",
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        folder = await file_service.create_folder(
            db,
            kb_id,
            parent_id,
            folder_name,
            principal,
        )
        data = file_service.file_to_data(folder)
    return _success(
        request,
        data,
        "Folder creation successful",
    )


@router.post("/file", response_model=SuccessEnvelope[dict[str, Any]])
async def upload_file(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    file: Annotated[UploadFile, UploadBody(...)],
) -> SuccessEnvelope[dict[str, Any]]:
    contents = await file.read()
    if not contents:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "The file is empty")
    if len(contents) > runtime.settings.max_file_size:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "File size exceeds limit")
    file_name = file.filename or ""
    file_ext = os.path.splitext(file_name)[1].lower()
    outcome = await _persist_upload(
        runtime,
        principal,
        kb_id=kb_id,
        parent_id=parent_id,
        file_name=file_name,
        file_ext=file_ext,
        content=contents,
        content_type=file.content_type,
        inherit_parser_config=True,
    )
    return _success(
        request,
        outcome.document_data,
        "File upload successful",
    )


@router.post("/customtext", response_model=SuccessEnvelope[dict[str, Any]])
async def custom_text(
    request: Request,
    create_data: CustomTextFileCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
) -> SuccessEnvelope[dict[str, Any]]:
    content = create_data.content.encode("utf-8")
    if not content:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "The content is empty")
    if len(content) > runtime.settings.max_file_size:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", "Content size exceeds limit")
    outcome = await _persist_upload(
        runtime,
        principal,
        kb_id=kb_id,
        parent_id=parent_id,
        file_name=f"{create_data.title}.txt",
        file_ext=".txt",
        content=content,
        content_type="text/plain",
        inherit_parser_config=False,
    )
    return _success(
        request,
        outcome.document_data,
        "custom text upload successful",
    )


@router.get("/{file_id}")
async def get_file(
    file_id: uuid.UUID,
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
    source: Annotated[KnowledgeRetrievalSource, Depends(get_source)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    original: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    async with runtime.database.async_session() as db:
        if principal is None:
            if source not in {
                KnowledgeRetrievalSource.EXTERNAL_API,
                KnowledgeRetrievalSource.MANAGER_API,
            }:
                raise KnowledgeError.from_code(
                    "KB_PRINCIPAL_INVALID",
                    "Knowledge principal is required",
                )
            file = await file_service.get_public_file(db, file_id)
        else:
            file = await file_service.get_file(db, file_id, principal)
        if file is None:
            raise file_service._not_found()
        snapshot = (file.file_key, file.file_name)
        qa_spec = None if original else await file_service.get_qa_export_spec(db, file)
    if qa_spec is not None:
        export = await _qa_export(runtime, qa_spec)
        if export is None:
            raise file_service._not_found("QA document has no exportable content")
        return StreamingResponse(
            iter_export_file(export.path),
            media_type=export.media_type,
            headers={
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{quote(export.filename)}"
                )
            },
            background=BackgroundTask(cleanup_export_file, export.path),
        )
    file_key, file_name = snapshot
    if not file_key:
        raise file_service._not_found("File has no storage key")
    media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    return StreamingResponse(
        KnowledgeFileStorage(runtime.storage).download_stream(file_key),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
    )


@router.post("/batch-download")
async def batch_download_files(
    request_body: BatchDownloadRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> StreamingResponse:
    requested_ids = set(request_body.file_ids)
    async with runtime.database.async_session() as db:
        result = await db.execute(
            select(File)
            .join(Knowledge, File.kb_id == Knowledge.id)
            .where(
                File.id.in_(requested_ids),
                Knowledge.workspace_id == principal.workspace_id,
                File.file_role == FILE_ROLE_SOURCE,
            )
        )
        files = list(result.scalars().all())
        if len(files) != len(requested_ids):
            raise file_service._not_found("File does not exist or access is denied")
        files = [file for file in files if file.file_key]
        if not files:
            raise file_service._not_found("Selected files have no storage key")
        specs = [await file_service.get_qa_export_spec(db, file) for file in files]
        snapshots = [file_service.stored_file_snapshot(file) for file in files]
    qa_exports = {}
    for file, spec in zip(snapshots, specs, strict=True):
        if spec and (export := await _qa_export(runtime, spec)):
            qa_exports[file.file_key] = export
    entries = file_service.build_zip_arcnames(snapshots)
    zip_name = file_service.make_zip_filename(snapshots, request_body.zip_filename)
    return StreamingResponse(
        file_service.stream_zip_files(
            entries,
            KnowledgeFileStorage(runtime.storage),
            qa_exports,
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}",
            "X-Total-Files": str(len(snapshots)),
        },
    )


@router.put("/{file_id}", response_model=SuccessEnvelope[dict[str, Any]])
async def update_file(
    request: Request,
    file_id: uuid.UUID,
    update_data: FileUpdate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        file = await file_service.update_file(db, file_id, update_data, principal)
        data = file_service.file_to_data(file)
    return _success(
        request,
        data,
        "File information updated successfully",
    )


@router.delete("/{file_id}", response_model=SuccessEnvelope[None])
async def delete_file(
    request: Request,
    file_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[None]:
    storage = KnowledgeFileStorage(runtime.storage)
    async with runtime.database.async_session() as db:
        plan = await file_service.prepare_file_deletion(db, file_id, principal)
    await file_service.delete_file_storage(storage, plan)
    async with runtime.database.async_session() as db:
        await file_service.persist_file_deletion(db, plan)
    return _success(request, msg="File deleted successfully")
