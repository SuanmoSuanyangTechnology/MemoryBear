"""Internal Chunk preview and CRUD routes migrated from the legacy controller."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from ...errors import KnowledgeError
from ...runtime import ProcessRuntime
from ...services import chunk as chunk_service
from ...services import file as file_service
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.knowledge_retrieval import KnowledgeRetrievalService
from ...services.knowledge_retrieval_preparation import KnowledgeRetrievalPreparation
from ...services.qa_import import (
    create_qa_import_resources,
    dispatch_qa_import,
    prepare_qa_import_resources,
    validate_qa_upload,
)
from ...tasks.dispatch import TaskDispatcher
from ..dependencies import Principal, get_principal, get_runtime, get_source
from ..schemas.chunk import (
    ChunkBatchCreate,
    ChunkCreate,
    ChunkRetrieve,
    ChunkUpdate,
    KnowledgeRetrievalSource,
    RetrievalPolicy,
    RetrievalPolicyRequest,
    RetrieveType,
)
from ..schemas.common import SuccessEnvelope, success
from ..schemas.knowledge_retrieval import KnowledgeRetrievalRequest

router = APIRouter(prefix="/chunks", tags=["chunks"])


def _success(
    _request: Request,
    data: Any = None,
    msg: str = "OK",
) -> dict[str, Any]:
    return success(data=data, msg=msg)


@router.get(
    "/{kb_id}/{document_id}/previewchunks",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def get_preview_chunks(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    page: Annotated[int, Query(gt=0)] = 1,
    pagesize: Annotated[int, Query(gt=0, le=100)] = 20,
    keywords: Annotated[str | None, Query()] = None,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
            include_file=True,
        )
        vision_config = await chunk_service.resolve_vision_config(
            db,
            snapshot,
            principal,
        )
    try:
        binary = await KnowledgeFileStorage(runtime.storage).download(snapshot.file_key or "")
    except Exception as exc:
        raise KnowledgeError.from_code(
            "KB_RESOURCE_NOT_FOUND",
            "File not found in storage",
        ) from exc
    parsed = await chunk_service.preview_with_vision(
        runtime,
        snapshot,
        binary,
        vision_config,
    )
    chunks = chunk_service.materialize_preview_chunks(snapshot, parsed)
    if keywords:
        chunks = [chunk for chunk in chunks if keywords in chunk.page_content]
    total = len(chunks)
    offset = (page - 1) * pagesize
    return _success(
        request,
        {
            "items": chunks[offset : offset + pagesize],
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "has_next": page * pagesize < total,
            },
        },
        (
            "Querying document chunk preview hierarchy succeeded"
            if snapshot.parent_child_mode
            else "Querying the document block preview list succeeded"
        ),
    )


@router.get(
    "/{kb_id}/{document_id}/chunks",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def get_chunks(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    page: Annotated[int, Query(gt=0)] = 1,
    pagesize: Annotated[int, Query(gt=0, le=100)] = 20,
    keywords: Annotated[str | None, Query()] = None,
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    store = chunk_service.build_chunk_store(
        runtime,
        await runtime.elasticsearch.client(),
        snapshot,
    )
    return _success(
        request,
        await chunk_service.list_chunks(
            store,
            snapshot,
            page=page,
            pagesize=pagesize,
            keywords=keywords,
        ),
        "Query of document chunk list succeeded",
    )


async def _mutation_store(
    runtime: ProcessRuntime,
    snapshot: chunk_service.ChunkDocumentSnapshot,
    principal: Principal,
):
    async with runtime.database.async_session() as db:
        resolved_embedding = await chunk_service.resolve_embedding_config(
            db,
            snapshot,
            principal,
        )
    return chunk_service.build_chunk_store(
        runtime,
        await runtime.elasticsearch.client(),
        snapshot,
        resolved_embedding,
    )


@router.post(
    "/{kb_id}/{document_id}/chunk",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def create_chunk(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    create_data: ChunkCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    chunk_service.validate_chunk_create(snapshot.parent_child_mode, create_data)
    store = await _mutation_store(runtime, snapshot, principal)
    _, latest = await store.search_by_segment(
        document_id=str(document_id),
        pagesize=1,
        page=1,
        asc=False,
    )
    current_sort_id = int(latest[0].metadata.get("sort_id", 0)) if latest else 0
    chunk = chunk_service.build_new_chunks(
        snapshot,
        [create_data],
        current_sort_id=current_sort_id,
    )[0]
    await store.add_chunks([chunk])
    async with runtime.database.async_session() as db:
        await chunk_service.update_document_chunk_count(db, snapshot, 1)
    await chunk_service.dispatch_graph_best_effort(snapshot)
    return _success(
        request,
        chunk.model_dump(mode="json"),
        "Document chunk creation successful",
    )


@router.post(
    "/{kb_id}/{document_id}/chunk/batch",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
)
async def create_chunks_batch(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    batch_data: ChunkBatchCreate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[list[dict[str, Any]]]:
    if len(batch_data.items) > runtime.settings.max_chunk_batch_size:
        raise KnowledgeError.from_code(
            "KB_VALIDATION_ERROR",
            f"Batch size exceeds limit: max {runtime.settings.max_chunk_batch_size}",
        )
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    for item in batch_data.items:
        chunk_service.validate_chunk_create(snapshot.parent_child_mode, item)
    store = await _mutation_store(runtime, snapshot, principal)
    _, latest = await store.search_by_segment(
        document_id=str(document_id),
        pagesize=1,
        page=1,
        asc=False,
    )
    current_sort_id = int(latest[0].metadata.get("sort_id", 0)) if latest else 0
    chunks = chunk_service.build_new_chunks(
        snapshot,
        batch_data.items,
        current_sort_id=current_sort_id,
    )
    await store.add_chunks(chunks)
    async with runtime.database.async_session() as db:
        await chunk_service.update_document_chunk_count(db, snapshot, len(chunks))
    await chunk_service.dispatch_graph_best_effort(snapshot)
    return _success(
        request,
        [chunk.model_dump(mode="json") for chunk in chunks],
        f"Batch created {len(chunks)} chunks successfully",
    )


@router.get(
    "/{kb_id}/{document_id}/{doc_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def get_chunk(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    store = chunk_service.build_chunk_store(
        runtime,
        await runtime.elasticsearch.client(),
        snapshot,
    )
    chunk = await chunk_service.require_owned_chunk(store, snapshot, doc_id)
    return _success(
        request,
        chunk.model_dump(mode="json"),
        "Document chunk query successful",
    )


@router.put(
    "/{kb_id}/{document_id}/{doc_id}",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def update_chunk(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_id: str,
    update_data: ChunkUpdate,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    store = await _mutation_store(runtime, snapshot, principal)
    chunk = await chunk_service.require_owned_chunk(store, snapshot, doc_id)
    chunk.page_content = update_data.chunk_content
    if update_data.is_qa:
        chunk.metadata.update(update_data.qa_metadata)
    await store.update_chunk(chunk)
    await chunk_service.dispatch_graph_best_effort(snapshot)
    return _success(
        request,
        chunk.model_dump(mode="json"),
        "The document chunk has been successfully updated",
    )


@router.delete("/{kb_id}/{document_id}/{doc_id}", response_model=SuccessEnvelope[None])
async def delete_chunk(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    force_refresh: Annotated[bool, Query()] = False,
) -> SuccessEnvelope[None]:
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    store = chunk_service.build_chunk_store(
        runtime,
        await runtime.elasticsearch.client(),
        snapshot,
    )
    await chunk_service.require_owned_chunk(store, snapshot, doc_id)
    await store.delete_by_ids([doc_id], refresh=force_refresh)
    async with runtime.database.async_session() as db:
        await chunk_service.update_document_chunk_count(db, snapshot, -1)
    await chunk_service.dispatch_graph_best_effort(snapshot)
    return _success(
        request,
        msg="The document chunk has been successfully deleted",
    )


@router.get("/retrieve_type", response_model=SuccessEnvelope[list[str]])
async def get_retrieve_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(
        request,
        list(RetrieveType),
        "Successfully obtained the retrieval type",
    )


@router.post(
    "/retrieval-policy",
    response_model=SuccessEnvelope[RetrievalPolicy],
)
async def get_retrieval_policy(
    request: Request,
    policy_request: RetrievalPolicyRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[RetrievalPolicy]:
    async with runtime.database.async_session() as db:
        policy = await KnowledgeRetrievalPreparation.prepare_policy_with_db(
            db,
            policy_request,
            principal,
        )
    return _success(
        request,
        policy.model_dump(mode="json"),
        "retrieval policy resolved",
    )


def _retrieval_request(
    retrieve_data: ChunkRetrieve,
    source: KnowledgeRetrievalSource,
) -> KnowledgeRetrievalRequest:
    payload = retrieve_data.model_dump(exclude_none=True)
    if (
        "vector_similarity_weight" in retrieve_data.model_fields_set
        and retrieve_data.vector_similarity_weight is None
    ):
        payload["vector_similarity_weight"] = None
    payload["knowledge_bases"] = [
        config.model_dump(include=config.model_fields_set | {"kb_id"})
        for config in retrieve_data.knowledge_bases
    ]
    payload["source"] = source
    return KnowledgeRetrievalRequest(**payload)


@router.post("/retrieval", response_model=SuccessEnvelope[Any])
async def retrieve_chunks(
    request: Request,
    retrieve_data: ChunkRetrieve,
    principal: Annotated[Principal, Depends(get_principal)],
    source: Annotated[KnowledgeRetrievalSource, Depends(get_source)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[Any]:
    retrieval_request = _retrieval_request(retrieve_data, source)
    if retrieve_data.metadata_filters_resolved:
        retrieval_request.mark_metadata_filters_resolved()
    result = await KnowledgeRetrievalService.retrieve_async(
        runtime,
        retrieval_request,
        principal,
    )
    data = result.model_dump(mode="json") if result.has_graph_data() else result.chunks
    return _success(request, data, "retrieval successful")


@router.post("/{kb_id}/import_qa", response_model=SuccessEnvelope[dict[str, str]])
async def import_qa_new_doc(
    request: Request,
    kb_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="CSV or Excel QA file")],
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
    parent_id: Annotated[uuid.UUID | None, Query()] = None,
) -> SuccessEnvelope[dict[str, str]]:
    filename = file.filename or ""
    content = await file.read()
    validate_qa_upload(filename, content)
    storage = KnowledgeFileStorage(runtime.storage)
    async with runtime.database.async_session() as db:
        plan = await prepare_qa_import_resources(
            db,
            storage,
            kb_id=kb_id,
            parent_id=parent_id,
            filename=filename,
            content=content,
            content_type=file.content_type,
            principal=principal,
        )
    await storage.upload(plan.file_key, content, file.content_type)
    persistence_succeeded = False
    try:
        async with runtime.database.async_session() as db:
            resources = await create_qa_import_resources(db, plan, principal)
            persistence_succeeded = True
    except Exception:
        if not persistence_succeeded:
            await file_service.compensate_storage_upload(storage, plan.file_key)
        raise
    task_id = await dispatch_qa_import(
        runtime,
        TaskDispatcher(),
        kb_id,
        resources.document_id,
        filename,
        content,
    )
    return _success(
        request,
        {
            "task_id": task_id,
            "document_id": str(resources.document_id),
            "file_id": str(resources.file_id),
        },
        "QA 导入任务已提交，后台处理中",
    )


@router.post(
    "/{kb_id}/{document_id}/import_qa",
    response_model=SuccessEnvelope[dict[str, str]],
)
async def import_qa_chunks(
    request: Request,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="CSV or Excel QA file")],
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, str]]:
    filename = file.filename or ""
    content = await file.read()
    validate_qa_upload(filename, content, require_non_empty=False)
    async with runtime.database.async_session() as db:
        snapshot = await chunk_service.get_chunk_document_snapshot(
            db,
            kb_id,
            document_id,
            principal,
        )
    task_id = await dispatch_qa_import(
        runtime,
        TaskDispatcher(),
        snapshot.knowledge_id,
        snapshot.document_id,
        filename,
        content,
    )
    return _success(
        request,
        {"task_id": task_id},
        "QA 导入任务已提交，后台处理中",
    )


__all__ = ["router"]
