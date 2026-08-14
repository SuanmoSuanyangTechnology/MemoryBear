import asyncio
import datetime
import json
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.core.utils.datetime_utils import utcnow_naive
from app.celery_app import celery_app
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_api_logger
from app.core.rag.common import settings
from app.core.rag.integrations.feishu.client import FeishuAPIClient
from app.core.rag.integrations.yuque.client import YuqueAPIClient
from app.core.rag.llm.chat_model import Base
from app.core.rag.knowledge_graph.config import (
    GraphPipeline,
    GraphPipelineConfigError,
    is_graph_enabled,
    resolve_graph_pipeline,
)
from app.core.rag.knowledge_graph.dispatch import (
    dispatch_graph_enabled_transition,
    dispatch_knowledge_graph_rebuild,
)
from app.core.rag.knowledge_graph.elasticsearch_store import (
    GraphElasticsearchStore,
)
from app.core.rag.parser_config import normalize_knowledge_parser_config_update
from app.core.rag.nlp import rag_tokenizer, search
from app.core.rag.prompts.generator import graph_entity_types
from app.core.rag.retrieval.async_elasticsearch import (
    AsyncElasticsearchClientProvider,
)
from app.core.rag.utils.redis_conn import REDIS_CONN
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorIndexOps
from app.core.response_utils import success, fail
from app.db import get_async_db, get_async_db_context
from app.dependencies import cur_workspace_access_guard_async, get_current_user_async
from app.models import knowledge_model
from app.models import file_model
from app.models.document_model import Document
from app.models.user_model import User
from app.schemas import knowledge_schema
from app.schemas import file_schema
from app.utils.redis_cache import invalidate_cache
from app.schemas.response_schema import ApiResponse
from app.repositories import knowledge_repository, knowledgeshare_repository
from app.services import knowledge_service, document_service
from app.services import file_service
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.services.model_service import ModelApiKeyService, ModelConfigService
from app.services.qa_export_service import (
    cleanup_qa_csv_export_file,
    iter_qa_csv_file_chunks,
    make_qa_export_filename,
    write_qa_csv_export_file,
)
from app.core.quota_stub import check_knowledge_capacity_quota

# Obtain a dedicated API logger
api_logger = get_api_logger()

_PARSE_DOCUMENT_TASK_NAME = "app.core.rag.tasks.parse_document"
_IMPORT_QA_TASK_NAME = "app.core.rag.tasks.import_qa_chunks"
_PARSE_TASK_KEY = "doc:{doc_id}:parse_task"
_PARSE_TASK_TTL = 7200
_SHARE_MIRRORED_MODEL_FIELDS = (
    ("embedding_id", "embedding"),
    ("reranker_id", "reranker"),
    ("llm_id", "llm"),
    ("image2text_id", "image2text"),
)

router = APIRouter(
    prefix="/knowledges",
    tags=["knowledges"],
    dependencies=[Depends(get_current_user_async)]  # Apply auth to all routes in this controller
)


async def _dispatch_reparse_tasks_for_knowledge_async(
        db: AsyncSession,
        knowledge_id: uuid.UUID,
) -> dict[str, int]:
    """Dispatch parse tasks for all active documents in a knowledge base using async DB reads."""
    result = {"queued": 0, "skipped": 0, "already_running": 0, "failed": 0}

    rows_result = await db.execute(
        select(Document, file_model.File)
        .join(file_model.File, Document.file_id == file_model.File.id)
        .where(
            Document.kb_id == knowledge_id,
            Document.status == 1,
        )
    )
    rows = rows_result.all()

    for db_document, db_file in rows:
        file_key = getattr(db_file, "file_key", None)
        if not file_key:
            result["skipped"] += 1
            api_logger.warning(
                "Skip document reparse because file has no storage key",
                extra={
                    "knowledge_id": str(knowledge_id),
                    "document_id": str(db_document.id),
                    "file_id": str(db_document.file_id),
                },
            )
            continue

        task_key = _PARSE_TASK_KEY.format(doc_id=db_document.id)
        try:
            claimed = REDIS_CONN.REDIS.set(task_key, "CLAIMED", ex=_PARSE_TASK_TTL, nx=True)
        except Exception as exc:
            result["failed"] += 1
            api_logger.warning(
                "Failed to claim document reparse task",
                extra={
                    "knowledge_id": str(knowledge_id),
                    "document_id": str(db_document.id),
                    "error": str(exc),
                },
            )
            continue

        if not claimed:
            result["already_running"] += 1
            continue

        file_name = getattr(db_file, "file_name", None) or db_document.file_name
        parser_config = db_document.parser_config or {}
        is_qa_doc = parser_config.get("doc_type") == "qa"

        try:
            if is_qa_doc:
                task = celery_app.send_task(
                    _IMPORT_QA_TASK_NAME,
                    args=[str(knowledge_id), str(db_document.id), file_name],
                    kwargs={"file_key": file_key, "clear_parse_task": True},
                    queue="qa_import",
                )
            else:
                task = celery_app.send_task(
                    _PARSE_DOCUMENT_TASK_NAME,
                    args=[file_key, db_document.id, file_name],
                )
        except Exception as exc:
            result["failed"] += 1
            REDIS_CONN.delete(task_key)
            api_logger.error(
                "Failed to dispatch document reparse task",
                extra={
                    "knowledge_id": str(knowledge_id),
                    "document_id": str(db_document.id),
                    "file_key": file_key,
                    "error": str(exc),
                },
            )
            continue

        REDIS_CONN.set(task_key, task.id, exp=_PARSE_TASK_TTL)
        result["queued"] += 1

    return result


async def _build_knowledge_detail_data_async(
        db: AsyncSession,
        db_knowledge: knowledge_model.Knowledge,
) -> dict:
    data = jsonable_encoder(knowledge_schema.Knowledge.model_validate(db_knowledge))
    if db_knowledge.permission_id != knowledge_model.PermissionType.Share:
        return data

    knowledgeshare = await knowledgeshare_repository.get_knowledgeshare_by_id_async(db, db_knowledge.id)
    if not knowledgeshare:
        api_logger.warning(
            "Share relation not found when mirroring knowledge model fields: knowledge_id=%s",
            db_knowledge.id,
        )
        return data

    source_knowledge = await knowledge_repository.get_knowledge_by_id_async(db=db, knowledge_id=knowledgeshare.source_kb_id)
    if not source_knowledge or source_knowledge.status == 2:
        api_logger.warning(
            "Source knowledge not available when mirroring share model fields: "
            "target_kb_id=%s, source_kb_id=%s",
            db_knowledge.id,
            knowledgeshare.source_kb_id,
        )
        return data

    source_data = jsonable_encoder(knowledge_schema.Knowledge.model_validate(source_knowledge))
    for id_field, model_field in _SHARE_MIRRORED_MODEL_FIELDS:
        data[id_field] = source_data.get(id_field)
        data[model_field] = source_data.get(model_field)
    return data


@router.get("/knowledgetype", response_model=ApiResponse)
def get_knowledge_types():
    return success(msg="Successfully obtained the knowledge type", data=list(knowledge_model.KnowledgeType))


@router.get("/permissiontype", response_model=ApiResponse)
def get_permission_types():
    return success(msg="Successfully obtained the knowledge permission type", data=list(knowledge_model.PermissionType))


@router.get("/parsertype", response_model=ApiResponse)
def get_parser_types():
    return success(msg="Successfully obtained the knowledge parser type", data=list(knowledge_model.ParserType))


@router.get("/knowledge_graph_entity_types", response_model=ApiResponse)
async def get_knowledge_graph_entity_types(
        llm_id: uuid.UUID,
        scenario: str,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    get knowledge graph entity types based on llm_id
    """
    api_logger.info(f"Obtain details of the knowledge graph: llm_id={llm_id}, username: {current_user.username}")

    try:
        # 1. Check whether the model exists
        api_logger.debug(f"Check whether the model exists: {llm_id}")
        config = await ModelConfigService.get_model_by_id_async(db=db, model_id=llm_id)
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model config does not exist",
            )
        api_key = await ModelApiKeyService.get_available_api_key_async(
            db,
            llm_id,
            tenant_id=current_user.tenant_id,
        )
        if api_key is None or not api_key.api_key:
            api_logger.warning(
                "No available API key for graph entity type generation"
                " llm_id=%s username=%s",
                str(llm_id),
                current_user.username,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No available API key for the selected model",
            )
        # 2. Prepare to configure chat_mdl information
        chat_model = Base(
            key=api_key.api_key,
            model_name=api_key.model_name,
            base_url=api_key.api_base
        )
        # response = graph_entity_types(chat_model, scenario)
        response = await asyncio.to_thread(graph_entity_types, chat_model, scenario)
        return success(data=response, msg="Successfully obtained knowledge graph entity types")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"get knowledge graph entity types failed: llm_id={llm_id} - {str(e)}")
        raise


@router.get("/knowledges", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_knowledges(
        parent_id: Optional[uuid.UUID] = Query(None, description="parent folder id"),
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (knowledge base name)"),
        kb_ids: Optional[str] = Query(None, description="Knowledge base ids, separated by commas"),
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Query the knowledge base list in pages
    - Support filtering by parent_id
    -  Support keyword search for knowledge base names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    api_logger.info(f"Query knowledge base list: workspace_id={current_user.current_workspace_id}, page={page}, pagesize={pagesize}, keywords={keywords}, kb_ids={kb_ids}, username: {current_user.username}")

    # 1. parameter validation
    if page < 1 or pagesize < 1:
        api_logger.warning(f"Error in paging parameters: page={page}, pagesize={pagesize}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Construct query conditions
    filters = [
        knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id
    ]

    # Keyword search (fuzzy matching of knowledge base name)
    if keywords:
        api_logger.debug(f"Add keyword search criteria: {keywords}")
        filters.append(
            or_(
                knowledge_model.Knowledge.name.ilike(f"%{keywords}%"),
                knowledge_model.Knowledge.description.ilike(f"%{keywords}%")
            )
        )
    # Knowledge base ids
    if kb_ids:
        filters.append(knowledge_model.Knowledge.id.in_(kb_ids.split(',')))
    else:
        filters.append(knowledge_model.Knowledge.status != 2)
        if parent_id:
            filters.append(knowledge_model.Knowledge.parent_id == parent_id)
        else:
            filters.append(knowledge_model.Knowledge.parent_id == current_user.current_workspace_id)
    filters.append(knowledge_model.Knowledge.permission_id != knowledge_model.PermissionType.Memory)
    # 3. Execute paged query
    try:
        api_logger.debug("Start executing knowledge base paging query")
        total, items = await knowledge_service.get_knowledges_paginated_async(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            current_user=current_user
        )
        api_logger.info(f"Knowledge base query successful: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"Knowledge base query failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )

    # 4. Return structured response
    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page*pagesize < total else False
        }
    }
    return success(data=jsonable_encoder(result), msg="Query of knowledge base list successful")


@router.post("/knowledge", response_model=ApiResponse)
@cur_workspace_access_guard_async()
@check_knowledge_capacity_quota
async def create_knowledge(
        create_data: knowledge_schema.KnowledgeCreate,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    create knowledge
    """
    api_logger.info(f"Request to create a knowledge base: name={create_data.name}, workspace_id={current_user.current_workspace_id}, username: {current_user.username}")

    try:
        create_data.workspace_id = current_user.current_workspace_id
        if create_data.parent_id and create_data.parent_id != current_user.current_workspace_id:
            parent = await knowledge_service.get_knowledge_by_id_async(
                db=db,
                knowledge_id=create_data.parent_id,
                current_user=current_user,
            )
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="The parent knowledge base does not exist or access is denied",
                )
        api_logger.debug(f"Start creating the knowledge base: {create_data.name}")
        # 1. Check if the knowledge base name already exists
        db_knowledge_exist = await knowledge_service.get_knowledge_by_name_async(db, name=create_data.name, current_user=current_user)
        if db_knowledge_exist:
            api_logger.warning(f"The knowledge base name already exists: {create_data.name}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The knowledge base name already exists: {create_data.name}"
            )
        db_knowledge = await knowledge_service.create_knowledge_async(db=db, knowledge=create_data, current_user=current_user)
        api_logger.info(f"The knowledge base has been successfully created: {db_knowledge.name} (ID: {db_knowledge.id})")
        return success(data=jsonable_encoder(knowledge_schema.Knowledge.model_validate(db_knowledge)), msg="The knowledge base has been successfully created")
    except GraphPipelineConfigError as e:
        api_logger.warning(
            "Invalid graph pipeline configuration during knowledge creation"
            " knowledge_name=%s error_type=%s",
            create_data.name,
            type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"The creation of the knowledge base failed: {create_data.name} - {str(e)}")
        raise


@router.get("/{knowledge_id}", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_knowledge(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Retrieve knowledge base information based on knowledge_id
    """
    api_logger.info(f"Obtain details of the knowledge base: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Query knowledge base information from the database
        api_logger.debug(f"Query knowledge base: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or access is denied: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or access is denied"
            )

        api_logger.info(f"Knowledge base query successful: {db_knowledge.name} (ID: {db_knowledge.id})")
        return success(data=await _build_knowledge_detail_data_async(db, db_knowledge), msg="Successfully obtained knowledge base information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Knowledge base query failed: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.get("/{knowledge_id}/chunk-policy", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_knowledge_chunk_policy(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    查询知识库的分块策略锁定状态
    - 知识库为空（无文档）→ parent_child_mode: null，未锁定，可自由选择
    - 知识库有文档且使用普通分块 → parent_child_mode: false，锁定为普通模式
    - 知识库有文档且使用父子分块 → parent_child_mode: true，锁定为父子模式
    """
    api_logger.info(f"Query knowledge base chunk policy: knowledge_id={knowledge_id}, username: {current_user.username}")

    # 1. 验证知识库存在
    db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    # 2. 查询该知识库下第一个文档的 parser_config（同 KB 下文档分块策略一致，取第一个即可）
    try:
        result_map = {0: None,    1: False,    2: True}
        api_logger.info(f"Knowledge base chunk policy: knowledge_id={knowledge_id}, parent_child_mode={result_map[db_knowledge.chunk_mode]}")
        return success(data={"parent_child_mode": result_map[db_knowledge.chunk_mode]}, msg="Successfully obtained knowledge base chunk policy")
    except Exception as e:
        api_logger.error(f"Failed to query knowledge base chunk policy: knowledge_id={knowledge_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query chunk policy: {str(e)}"
        )


@router.put("/{knowledge_id}", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def update_knowledge(
        knowledge_id: uuid.UUID,
        update_data: knowledge_schema.KnowledgeUpdate,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    api_logger.info(f"Update knowledge base request: knowledge_id={knowledge_id}, username: {current_user.username}")
    db_knowledge = await _update_knowledge(knowledge_id=knowledge_id, update_data=update_data, db=db, current_user=current_user)
    return success(data=jsonable_encoder(knowledge_schema.Knowledge.model_validate(db_knowledge)), msg="The knowledge base information has been successfully updated")


@router.get("/{kb_id}/qa/export")
@cur_workspace_access_guard_async()
async def export_knowledge_qa_csv(
        kb_id: uuid.UUID,
        current_user: User = Depends(get_current_user_async),
):
    """Export all active QA pairs in a knowledge base as a two-column CSV."""
    api_logger.info(f"KB QA CSV export: kb_id={kb_id}, username={current_user.username}")

    async with get_async_db_context() as db:
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(
            db, knowledge_id=kb_id, current_user=current_user
        )
        if not db_knowledge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it",
            )
        filename = make_qa_export_filename(db_knowledge.name)

    from urllib.parse import quote

    export_path = await asyncio.to_thread(write_qa_csv_export_file, kb_id)

    return StreamingResponse(
        iter_qa_csv_file_chunks(export_path),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
        background=BackgroundTask(cleanup_qa_csv_export_file, export_path),
    )


@router.post("/{kb_id}/batch-download")
@cur_workspace_access_guard_async()
async def kb_batch_download(
        kb_id: uuid.UUID,
        current_user: User = Depends(get_current_user_async),
        storage_service: FileStorageService = Depends(get_file_storage_service),
        request_body: file_schema.KBBatchDownloadRequest = file_schema.KBBatchDownloadRequest(),
):
    """知识库文件一键下载 — 将该知识库下所有文件打包为 ZIP 流式下载"""
    api_logger.info(f"KB batch download: kb_id={kb_id}, username={current_user.username}")

    async with get_async_db_context() as db:
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(
            db, knowledge_id=kb_id, current_user=current_user
        )
        if not db_knowledge:
            raise BusinessException("知识库不存在或无权访问", BizCode.NOT_FOUND)

        files_result = await db.execute(
            select(file_model.File).where(
                file_model.File.kb_id == kb_id,
                file_model.File.file_role == file_model.FILE_ROLE_SOURCE,
                file_model.File.file_key.isnot(None),
                file_model.File.file_key != "",
            )
        )
        files = list(files_result.scalars().all())

        if not files:
            raise BusinessException("该知识库下没有可下载的文件", BizCode.NOT_FOUND)

        qa_export_specs: dict[str, file_service.QAExportSpec] = {}
        for f in files:
            doc_result = await db.execute(select(Document).where(Document.file_id == f.id))
            doc = doc_result.scalars().first()
            if doc and (doc.parser_config or {}).get("doc_type") == "qa":
                qa_export_specs[f.file_key] = file_service.QAExportSpec(
                    kb_id=kb_id,
                    document_id=doc.id,
                    file_ext=f.file_ext,
                    file_name=f.file_name,
                )

        entries = file_service.build_zip_arcnames(files)
        zip_name = file_service.make_zip_filename(files, request_body.zip_filename, base_name=db_knowledge.name)
        total_files = len(files)

    from urllib.parse import quote
    return StreamingResponse(
        file_service.stream_zip_files(
            entries,
            storage_service,
            api_logger,
            qa_export_specs=qa_export_specs,
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}",
            "X-Total-Files": str(total_files),
        },
    )


async def _update_knowledge(
        knowledge_id: uuid.UUID,
        update_data: knowledge_schema.KnowledgeUpdate,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
) -> knowledge_schema.Knowledge:
    """
    Update knowledge base information
    """
    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Query the knowledge base to be updated: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        # 2. If updating the embedding_id, delete the knowledge base vector index, reset all document parsing progress to 0, and set chunk_num to 0
        update_dict = update_data.model_dump(exclude_unset=True)
        if "parent_id" in update_dict:
            parent_id = update_dict["parent_id"]
            if parent_id is not None and parent_id != current_user.current_workspace_id:
                parent = await knowledge_service.get_knowledge_by_id_async(
                    db=db,
                    knowledge_id=parent_id,
                    current_user=current_user,
                )
                if not parent:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="The parent knowledge base does not exist or access is denied",
                    )
        graph_enabled_before: bool | None = None
        if "parser_config" in update_dict:
            try:
                graph_enabled_before = is_graph_enabled(
                    db_knowledge.parser_config
                )
                update_dict["parser_config"] = normalize_knowledge_parser_config_update(
                    db_knowledge.parser_config,
                    update_dict["parser_config"],
                )
            except GraphPipelineConfigError as exc:
                api_logger.warning(
                    "Invalid graph pipeline configuration during knowledge update"
                    " knowledge_id=%s error_type=%s",
                    str(knowledge_id),
                    type(exc).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
        embedding_changed = False
        if "name" in update_dict:
            name = update_dict["name"]
            if name != db_knowledge.name:
                # Check if the knowledge base name already exists
                db_knowledge_exist = await knowledge_service.get_knowledge_by_name_async(db, name=name, current_user=current_user)
                if db_knowledge_exist:
                    api_logger.warning(f"The knowledge base name already exists: {name}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"The knowledge base name already exists: {name}"
                    )
        if "embedding_id" in update_dict:
            embedding_id = update_dict["embedding_id"]
            if embedding_id != db_knowledge.embedding_id:
                embedding_changed = True
                if db_knowledge.embedding_id and db_knowledge.reranker_id:
                    await asyncio.to_thread(ElasticSearchVectorIndexOps.for_knowledge(db_knowledge.id).delete_index)
                await document_service.reset_documents_progress_by_kb_id_async(db, kb_id=db_knowledge.id, current_user=current_user)

        # 2. Update fields (only update non-null fields)
        api_logger.debug(f"Start updating the knowledge base fields: {knowledge_id}")
        updated_fields = []
        for field, value in update_dict.items():
            if hasattr(db_knowledge, field):
                old_value = getattr(db_knowledge, field)
                if old_value != value:
                    # update value
                    setattr(db_knowledge, field, value)
                    if field == "parser_config":
                        updated_fields.append("parser_config: changed")
                    else:
                        updated_fields.append(f"{field}: {old_value} -> {value}")

        if embedding_changed and db_knowledge.chunk_num != 0:
            old_chunk_num = db_knowledge.chunk_num
            db_knowledge.chunk_num = 0
            updated_fields.append(f"chunk_num: {old_chunk_num} -> 0")

        if updated_fields:
            api_logger.debug(f"updated fields: {', '.join(updated_fields)}")

        db_knowledge.updated_at = utcnow_naive()

        # 3. Save to database
        await db.commit()
        await db.refresh(db_knowledge)
        api_logger.info(f"The knowledge base has been successfully updated: {db_knowledge.name} (ID: {db_knowledge.id})")

        if db_knowledge.name == "USER_RAG_MERORY":
            try:
                await invalidate_cache(prefix=f"storage_type:{db_knowledge.workspace_id}")
            except Exception:
                pass

        if graph_enabled_before is not None:
            try:
                graph_task = dispatch_graph_enabled_transition(
                    str(db_knowledge.id),
                    graph_enabled_before,
                    db_knowledge.parser_config,
                )
                if graph_task is not None:
                    current_enabled = is_graph_enabled(db_knowledge.parser_config)
                    pipeline = resolve_graph_pipeline(db_knowledge.parser_config)
                    api_logger.info(
                        "Knowledge graph enablement task accepted"
                        " knowledge_id=%s previous_enabled=%s"
                        " current_enabled=%s pipeline=%s task_id=%s",
                        str(db_knowledge.id),
                        str(graph_enabled_before).lower(),
                        str(current_enabled).lower(),
                        pipeline.value,
                        str(getattr(graph_task, "id", None) or "unknown"),
                    )
            except Exception as dispatch_error:
                api_logger.error(
                    "Failed to dispatch graph task after enablement change"
                    " knowledge_id=%s error_type=%s",
                    str(db_knowledge.id),
                    type(dispatch_error).__name__,
                )

        if embedding_changed:
            try:
                dispatch_result = await _dispatch_reparse_tasks_for_knowledge_async(db=db, knowledge_id=db_knowledge.id)
                api_logger.info(
                    "Knowledge base embedding changed, document reparse tasks dispatched",
                    extra={
                        "knowledge_id": str(db_knowledge.id),
                        **dispatch_result,
                    },
                )
            except Exception as dispatch_error:
                api_logger.error(
                    "Failed to dispatch document reparse tasks after embedding change",
                    extra={
                        "knowledge_id": str(db_knowledge.id),
                        "error": str(dispatch_error),
                    },
                )

        # 4. Return the updated knowledge base with async-safe relationships loaded
        return await knowledge_repository.get_knowledge_by_id_async(db=db, knowledge_id=db_knowledge.id) or db_knowledge
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        api_logger.error(f"Knowledge base update failed: knowledge_id={knowledge_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge base update failed: {str(e)}"
        )


@router.delete("/{knowledge_id}", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def delete_knowledge(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Soft-delete knowledge base
    """
    api_logger.info(f"Request to delete knowledge base: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        # 2. Soft-delete knowledge base
        api_logger.debug(f"Perform a soft delete: {db_knowledge.name} (ID: {knowledge_id})")
        db_knowledge.status = 2
        db_knowledge.updated_at = utcnow_naive()
        await db.commit()
        api_logger.info(f"The knowledge base has been successfully deleted: {db_knowledge.name} (ID: {knowledge_id})")

        if db_knowledge.name == "USER_RAG_MERORY":
            try:
                await invalidate_cache(prefix=f"storage_type:{db_knowledge.workspace_id}")
            except Exception:
                pass

        return success(msg="The knowledge base has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the knowledge base: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.get("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def get_knowledge_graph(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Retrieve knowledge_graph base information based on knowledge_id
    """
    api_logger.info(f"Obtain details of the knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Query knowledge base information from the database
        api_logger.debug(f"Query knowledge base: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or access is denied: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or access is denied"
            )

        if resolve_graph_pipeline(
            db_knowledge.parser_config
        ) is GraphPipeline.EVIDENCE:
            client = await AsyncElasticsearchClientProvider.get_shared_client()
            graph = await GraphElasticsearchStore(
                client
            ).load_projection_graph(
                search.index_name(str(db_knowledge.workspace_id)),
                str(db_knowledge.id),
                node_limit=256,
                edge_limit=128,
            )
            return success(
                data={"graph": graph, "mind_map": {}},
                msg="Successfully obtained knowledge graph information",
            )

        req = {
            "kb_id": [str(db_knowledge.id)],
            "knowledge_graph_kwd": ["graph"]
        }

        obj = {"graph": {}, "mind_map": {}}
        index_exists = await asyncio.to_thread(
            settings.docStoreConn.indexExist,
            search.index_name(str(db_knowledge.workspace_id)),
            str(db_knowledge.id),
        )
        if not index_exists:
            return success(data=obj, msg="Successfully obtained knowledge graph information")
        sres = await asyncio.to_thread(
            settings.retriever.search,
            req,
            search.index_name(str(db_knowledge.workspace_id)),
            [str(db_knowledge.id)],
        )
        if not len(sres.ids):
            return success(data=obj, msg="Successfully obtained knowledge graph information")

        for id in sres.ids[:1]:
            ty = sres.field[id]["knowledge_graph_kwd"]
            try:
                content_json = json.loads(sres.field[id]["page_content"])
            except Exception:
                continue

            obj[ty] = content_json

        if "nodes" in obj["graph"]:
            obj["graph"]["nodes"] = sorted(obj["graph"]["nodes"], key=lambda x: x.get("pagerank", 0), reverse=True)[:256]
            if "edges" in obj["graph"]:
                node_id_set = {o["id"] for o in obj["graph"]["nodes"]}
                filtered_edges = [o for o in obj["graph"]["edges"] if o["source"] != o["target"] and o["source"] in node_id_set and o["target"] in node_id_set]
                obj["graph"]["edges"] = sorted(filtered_edges, key=lambda x: x.get("weight", 0), reverse=True)[:128]
        return success(data=obj, msg="Successfully obtained knowledge graph information")
    except GraphPipelineConfigError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Knowledge graph query failed: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.delete("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def delete_knowledge_graph(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    delete knowledge graph
    """
    api_logger.info(f"Request to delete knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        task = celery_app.send_task(
            "app.core.rag.tasks.clear_all_knowledge_graph_data",
            args=[str(knowledge_id)],
            kwargs={"force": True},
        )
        api_logger.info(
            "Knowledge graph cleanup task accepted"
            " knowledge_id=%s task_id=%s",
            str(knowledge_id),
            str(task.id),
        )
        return success(
            data={"task_id": task.id},
            msg="Task accepted. Knowledge graph cleanup is being processed in the background.",
        )
    except Exception as e:
        api_logger.error(f"Failed to delete from the knowledge base: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.post("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def rebuild_knowledge_graph(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    rebuild knowledge graph
    """
    api_logger.info(f"Request to rebuild knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(
                f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        pipeline = resolve_graph_pipeline(db_knowledge.parser_config)
        if pipeline is GraphPipeline.LEGACY:
            await asyncio.to_thread(
                settings.docStoreConn.delete,
                {
                    "knowledge_graph_kwd": [
                        "graph",
                        "subgraph",
                        "entity",
                        "relation",
                    ]
                },
                search.index_name(str(db_knowledge.workspace_id)),
                str(db_knowledge.id),
            )

        task_name = (
            "app.core.rag.tasks.build_graphrag_for_kb"
            if pipeline is GraphPipeline.LEGACY
            else "app.core.rag.tasks.rebuild_evidence_graph_knowledge"
        )
        task = dispatch_knowledge_graph_rebuild(
            str(knowledge_id),
            db_knowledge.parser_config,
        )
        if task is None:
            raise GraphPipelineConfigError("knowledge graph is not enabled")
        api_logger.info(
            "Knowledge graph rebuild task accepted"
            " kb_id=%s pipeline=%s task=%s task_id=%s",
            str(knowledge_id),
            pipeline.value,
            task_name,
            str(task.id),
        )
        result = {
            "task_id": task.id
        }
        return success(data=result, msg="Task accepted. rebuild knowledge graph is being processed in the background.")
    except GraphPipelineConfigError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        api_logger.error(f"Failed to rebuild knowledge graph: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.get("/check/yuque/auth", response_model=ApiResponse)
async def check_yuque_auth(
        yuque_user_id: str,
        yuque_token: str,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    check yuque auth info
    """
    api_logger.info(f"check yuque auth info, username: {current_user.username}")

    try:
        api_client = YuqueAPIClient(
            user_id=yuque_user_id,
            token=yuque_token
        )
        async with api_client as client:
            repos = await client.get_user_repos()
            if repos:
                return success(msg="Successfully auth yuque info")
        return fail(BizCode.UNAUTHORIZED, msg="auth yuque info failed", error="user_id or token is incorrect")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"auth yuque info failed: {str(e)}")
        raise


@router.get("/check/feishu/auth", response_model=ApiResponse)
async def check_feishu_auth(
        feishu_app_id: str,
        feishu_app_secret: str,
        feishu_folder_token: str,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    check feishu auth info
    """
    api_logger.info(f"check feishu auth info, username: {current_user.username}")

    try:
        api_client = FeishuAPIClient(
            app_id=feishu_app_id,
            app_secret=feishu_app_secret
        )
        async with api_client as client:
            files = await client.list_all_folder_files(feishu_folder_token, recursive=True)
            if files:
                return success(msg="Successfully auth feishu info")
        return fail(BizCode.UNAUTHORIZED, msg="auth feishu info failed", error="app_id or app_secret or feishu_folder_token is incorrect")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"auth feishu info failed: {str(e)}")
        raise


@router.post("/{knowledge_id}/sync", response_model=ApiResponse)
@cur_workspace_access_guard_async()
async def sync_knowledge(
        knowledge_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    sync knowledge base information based on knowledge_id
    """
    api_logger.info(f"Obtain details of the knowledge base: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Query knowledge base information from the database
        api_logger.debug(f"Query knowledge base: {knowledge_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=knowledge_id, current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or access is denied: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or access is denied"
            )

        # 2. sync knowledge
        # from app.tasks import sync_knowledge_for_kb
        # sync_knowledge_for_kb(kb_id)
        task = celery_app.send_task("app.core.rag.tasks.sync_knowledge_for_kb", args=[knowledge_id])
        result = {
             "task_id": task.id
        }
        return success(data=result, msg="Task accepted. sync knowledge is being processed in the background.")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Failed to sync knowledge: knowledge_id={knowledge_id} - {str(e)}")
        raise
