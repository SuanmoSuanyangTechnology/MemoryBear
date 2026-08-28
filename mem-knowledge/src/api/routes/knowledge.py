"""Internal Knowledge routes migrated from the legacy controller."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from redbear_model import (
    ModelConfigNotFoundError,
    ModelCredentialNotFoundError,
    PublicCredentialUnavailableError,
    resolve_model_async,
)
from sqlalchemy import select
from starlette.background import BackgroundTask

from ...errors import KnowledgeError
from ...models.owned import FILE_ROLE_SOURCE, File, KnowledgeType, ParserType, PermissionType
from ...rag.integrations.feishu import FeishuAPIClient
from ...rag.integrations.yuque import YuqueAPIClient
from ...rag.knowledge_graph.elasticsearch_store import GraphElasticsearchStore
from ...rag.retrieval.async_elasticsearch import collection_name_for_knowledge
from ...repositories.model_registry import AsyncSQLModelRegistry
from ...runtime import ProcessRuntime
from ...services import file as file_service
from ...services import graph as graph_service
from ...services import knowledge as knowledge_service
from ...services.knowledge_commands import (
    dispatch_graph_transition,
    dispatch_reparse_snapshots,
    dispatch_sync,
    load_reparse_snapshots,
)
from ...services.knowledge_file_storage import KnowledgeFileStorage
from ...services.qa_export import (
    cleanup_export_file,
    iter_export_file,
    make_qa_export_filename,
    write_document_export,
    write_knowledge_csv,
)
from ...tasks.dispatch import TaskDispatcher
from ..dependencies import Principal, get_principal, get_runtime
from ..schemas.common import SuccessEnvelope, fail, success
from ..schemas.file import KBBatchDownloadRequest
from ..schemas.knowledge import KnowledgeCreate, KnowledgeUpdate

router = APIRouter(
    prefix="/knowledges",
    tags=["knowledges"],
    dependencies=[Depends(get_principal)],
)

logger = logging.getLogger(__name__)


def _success(
    _request: Request,
    data: Any = None,
    msg: str = "OK",
) -> dict[str, Any]:
    return success(data=data, msg=msg)


@router.get("/knowledgetype", response_model=SuccessEnvelope[list[str]])
async def get_knowledge_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(
        request,
        list(KnowledgeType),
        "Successfully obtained the knowledge type",
    )


@router.get("/permissiontype", response_model=SuccessEnvelope[list[str]])
async def get_permission_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(
        request,
        list(PermissionType),
        "Successfully obtained the knowledge permission type",
    )


@router.get("/parsertype", response_model=SuccessEnvelope[list[str]])
async def get_parser_types(request: Request) -> SuccessEnvelope[list[str]]:
    return _success(
        request,
        list(ParserType),
        "Successfully obtained the knowledge parser type",
    )


@router.get("/knowledge_graph_entity_types", response_model=SuccessEnvelope[str])
async def get_knowledge_graph_entity_types(
    request: Request,
    llm_id: uuid.UUID,
    scenario: str,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[str]:
    async with runtime.database.async_session() as db:
        try:
            resolved = await resolve_model_async(
                AsyncSQLModelRegistry(db),
                model_config_id=llm_id,
                tenant_id=principal.tenant_id,
            )
        except ModelConfigNotFoundError as exc:
            raise KnowledgeError.from_code(
                "KB_RESOURCE_NOT_FOUND",
                "Model config does not exist",
                status_code=404,
                response_code=404,
                response_style="http",
            ) from exc
        except (
            ModelCredentialNotFoundError,
            PublicCredentialUnavailableError,
        ) as exc:
            raise KnowledgeError.from_code(
                "KB_MODEL_UNAVAILABLE",
                "No available API key for the selected model",
                status_code=400,
                response_code=400,
                response_style="http",
            ) from exc
    result = await graph_service.graph_entity_types(runtime, resolved, scenario)
    return _success(
        request,
        result,
        "Successfully obtained knowledge graph entity types",
    )


@router.get("/check/yuque/auth", response_model=SuccessEnvelope[None])
async def check_yuque_auth(
    request: Request,
    yuque_user_id: str,
    yuque_token: str,
) -> SuccessEnvelope[None]:
    async with YuqueAPIClient(yuque_user_id, yuque_token) as client:
        repositories = await client.get_user_repos()
    if not repositories:
        return fail(
            2001,
            msg="auth yuque info failed",
            error="user_id or token is incorrect",
        )
    return _success(request, msg="Successfully auth yuque info")


@router.get("/check/feishu/auth", response_model=SuccessEnvelope[None])
async def check_feishu_auth(
    request: Request,
    feishu_app_id: str,
    feishu_app_secret: str,
    feishu_folder_token: str,
) -> SuccessEnvelope[None]:
    async with FeishuAPIClient(feishu_app_id, feishu_app_secret) as client:
        files = await client.list_all_folder_files(
            feishu_folder_token,
            recursive=True,
        )
    if not files:
        return fail(
            2001,
            msg="auth feishu info failed",
            error="app_id or app_secret or feishu_folder_token is incorrect",
        )
    return _success(request, msg="Successfully auth feishu info")


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
        "Query of knowledge base list successful",
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
    return _success(
        request,
        data,
        "The knowledge base has been successfully created",
    )


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
    return _success(
        request,
        data,
        "Successfully obtained knowledge base information",
    )


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
        "Successfully obtained knowledge base chunk policy",
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
        plan = await knowledge_service.prepare_knowledge_update(
            db,
            knowledge_id,
            update_data,
            principal,
        )
    if plan.delete_vector_index:
        await (await runtime.elasticsearch.client()).indices.delete(
            index=collection_name_for_knowledge(knowledge_id),
            ignore_unavailable=True,
        )
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.apply_knowledge_update(
            db,
            plan,
            principal,
            runtime.redis,
        )
        data = await knowledge_service.knowledge_to_data(db, knowledge)
    dispatcher = TaskDispatcher()
    if plan.graph_enabled_before is not None:
        try:
            await dispatch_graph_transition(
                dispatcher,
                knowledge_id,
                plan.graph_enabled_before,
                knowledge.parser_config,
            )
        except Exception:
            logger.error(
                "Failed to dispatch graph transition knowledge_id=%s",
                knowledge_id,
            )
    if plan.embedding_changed:
        try:
            async with runtime.database.async_session() as db:
                snapshots, skipped = await load_reparse_snapshots(db, knowledge_id)
            await dispatch_reparse_snapshots(
                await runtime.redis.client(),
                dispatcher,
                snapshots,
                skipped=skipped,
            )
        except Exception:
            logger.error(
                "Failed to dispatch reparse tasks knowledge_id=%s",
                knowledge_id,
            )
    return _success(
        request,
        data,
        "The knowledge base information has been successfully updated",
    )


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
    return _success(
        request,
        msg="The knowledge base has been successfully deleted",
    )


@router.get(
    "/{knowledge_id}/knowledge_graph",
    response_model=SuccessEnvelope[dict[str, Any]],
)
async def get_knowledge_graph(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, Any]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
    store = GraphElasticsearchStore(await runtime.elasticsearch.client())
    try:
        data = await graph_service.get_graph(knowledge, store)
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", str(exc)) from exc
    return _success(
        request,
        data,
        "Successfully obtained knowledge graph information",
    )


@router.delete(
    "/{knowledge_id}/knowledge_graph",
    response_model=SuccessEnvelope[dict[str, str]],
)
async def delete_knowledge_graph(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, str]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
    task_id = await graph_service.delete_graph(knowledge, TaskDispatcher())
    return _success(
        request,
        {"task_id": task_id},
        "Task accepted. Knowledge graph cleanup is being processed in the background.",
    )


@router.post(
    "/{knowledge_id}/knowledge_graph",
    response_model=SuccessEnvelope[dict[str, str]],
)
async def rebuild_knowledge_graph(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, str]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
    store = GraphElasticsearchStore(await runtime.elasticsearch.client())
    try:
        task_id = await graph_service.rebuild_graph(
            knowledge,
            store,
            TaskDispatcher(),
        )
    except ValueError as exc:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", str(exc)) from exc
    return _success(
        request,
        {"task_id": task_id},
        "Task accepted. rebuild knowledge graph is being processed in the background.",
    )


@router.post("/{knowledge_id}/sync", response_model=SuccessEnvelope[dict[str, str]])
async def sync_knowledge(
    request: Request,
    knowledge_id: uuid.UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    runtime: Annotated[ProcessRuntime, Depends(get_runtime)],
) -> SuccessEnvelope[dict[str, str]]:
    async with runtime.database.async_session() as db:
        knowledge = await knowledge_service.get_knowledge(db, knowledge_id, principal)
        if knowledge is None:
            raise knowledge_service._not_found()
    task_id = await dispatch_sync(TaskDispatcher(), knowledge_id)
    return _success(
        request,
        {"task_id": task_id},
        "Task accepted. sync knowledge is being processed in the background.",
    )


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
