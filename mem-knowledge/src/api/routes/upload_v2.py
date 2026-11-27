"""Versioned upload entrypoints. The original upload routes still accept duplicates."""

from os.path import splitext
from typing import Annotated
from uuid import UUID

from error_core.envelope import success
from error_core.knowledge_upload import (
    UploadCreated,
    UploadedDocument,
    UploadedQA,
    UploadKind,
    upload_locale,
    upload_responses,
)
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import RedirectResponse

from ...application.runtime import ProcessRuntime
from ...errors import KnowledgeError
from ...services import file as file_service
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.qa_import import dispatch_qa_import, prepare_qa_import_resources
from ...services.upload_deduplication import discard_unpublished_qa_upload, save_new_upload
from ...tasks.dispatch import TaskDispatcher, TaskNotPublishedError
from ..dependencies import Principal, get_principal, get_runtime, get_source
from ..knowledge_capacity import check_workspace_capacity
from ..schemas.chunk import KnowledgeRetrievalSource
from ..upload_contract import (
    GATEWAY_DOCUMENT_UPLOAD_PATH,
    GATEWAY_QA_UPLOAD_PATH,
    GATEWAY_UPLOAD_PREFIX,
)

router = APIRouter(tags=["knowledge-upload-v2"])
gateway_router = APIRouter(tags=["knowledge-upload-v2"])


@gateway_router.post(GATEWAY_DOCUMENT_UPLOAD_PATH + "/", include_in_schema=False)
@gateway_router.post(GATEWAY_QA_UPLOAD_PATH + "/", include_in_schema=False)
async def redirect_upload(
    request: Request,
    source: Annotated[KnowledgeRetrievalSource, Depends(get_source)],
) -> RedirectResponse:
    """尾斜杠请求重定向到调用方使用的入口，保留查询参数和 POST。"""
    path = request.url.path.removesuffix("/")
    if source is KnowledgeRetrievalSource.MANAGER_API:
        path = "/api" + path.removeprefix(GATEWAY_UPLOAD_PREFIX)
    elif source is KnowledgeRetrievalSource.EXTERNAL_API:
        path = "/v1" + path.removeprefix(GATEWAY_UPLOAD_PREFIX)
    if request.url.query:
        path += "?" + request.url.query
    return RedirectResponse(path, status_code=307)


async def read_upload(file: UploadFile, runtime: ProcessRuntime) -> bytes:
    content = await file.read(runtime.settings.max_file_size + 1)
    if not content:
        raise KnowledgeError.from_code("KB_UPLOAD_FILE_EMPTY")
    if len(content) > runtime.settings.max_file_size:
        raise KnowledgeError.from_code("KB_UPLOAD_FILE_SIZE_LIMIT")
    return content


@router.post(
    UploadKind.DOCUMENT, status_code=201, responses=upload_responses(outer_status=402),
    response_model=UploadCreated[UploadedDocument],
)
@gateway_router.post(
    GATEWAY_DOCUMENT_UPLOAD_PATH, status_code=201, responses=upload_responses(outer_status=402),
    response_model=UploadCreated[UploadedDocument],
)
async def upload_document(
    request: Request,
    kb_id: UUID,
    parent_id: UUID,
    file: Annotated[UploadFile, File()],
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> dict:
    await check_workspace_capacity(request, runtime, principal)
    content = await read_upload(file, runtime)
    filename = file.filename or ""
    storage = KnowledgeFileStorage(runtime.storage)
    async with runtime.database.async_session() as db:
        plan = await file_service.upload_content(
            db, storage, kb_id=kb_id, parent_id=parent_id, file_name=filename,
            file_ext=splitext(filename)[1].lower(), content=content,
            content_type=file.content_type, principal=principal, inherit_parser_config=True,
            record_fingerprint=True,
        )
    outcome = await save_new_upload(runtime, storage, plan, principal, content, file.content_type)
    return success(outcome.document_data, locale=upload_locale(request))


@router.post(
    UploadKind.QA, status_code=201, responses=upload_responses(qa=True),
    response_model=UploadCreated[UploadedQA],
)
@gateway_router.post(
    GATEWAY_QA_UPLOAD_PATH, status_code=201, responses=upload_responses(qa=True),
    response_model=UploadCreated[UploadedQA],
)
async def upload_qa(
    request: Request,
    kb_id: UUID,
    file: Annotated[UploadFile, File(description="CSV、XLSX 或 XLS 文件")],
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: Annotated[UUID | None, Query()] = None,
) -> dict:
    content = await read_upload(file, runtime)
    filename = file.filename or ""
    storage = KnowledgeFileStorage(runtime.storage)
    async with runtime.database.async_session() as db:
        plan = await prepare_qa_import_resources(
            db, storage, kb_id=kb_id, parent_id=parent_id, filename=filename,
            content=content, content_type=file.content_type, principal=principal,
        )
    outcome = await save_new_upload(runtime, storage, plan, principal, content, file.content_type)
    try:
        task_id = await dispatch_qa_import(
            runtime, TaskDispatcher(runtime=runtime), kb_id, outcome.document_id, filename,
            file_key=plan.file_key, connect_before_publish=True,
        )
    except TaskNotPublishedError:
        await discard_unpublished_qa_upload(runtime, storage, plan, outcome)
        raise
    return success(
        {"task_id": task_id, "document_id": outcome.document_id, "file_id": outcome.file_id},
        locale=upload_locale(request),
    )
