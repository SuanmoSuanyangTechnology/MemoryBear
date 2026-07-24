import asyncio
import copy
import datetime
import os
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.utils.datetime_utils import utcnow_naive
from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging_config import get_api_logger
from app.core.rag.knowledge_graph.dispatch import (
    dispatch_document_graph_sync,
    enqueue_document_graph_sync,
)
from app.core.rag.utils.redis_conn import REDIS_CONN
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorFactory,
    ElasticSearchVectorIndexOps,
)
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.core.response_utils import success
from app.db import get_async_db
from app.dependencies import get_current_user_async
from app.models import document_model
from app.models import file_model
from app.models.user_model import User
from app.schemas import document_schema
from app.schemas.response_schema import ApiResponse
from app.services import document_service, knowledge_service
from app.services.rag_access_service import require_current_workspace_document_async
from app.services.file_storage_service import FileStorageService, get_file_storage_service
from app.schemas import knowledge_metadata_schema as metadata_schema
from app.services.knowledge_metadata_service import KnowledgeMetadataService


# Obtain a dedicated API logger
api_logger = get_api_logger()

# Redis keys for document parse task tracking
_PARSE_TASK_KEY = "doc:{doc_id}:parse_task"
_PARSE_CANCEL_KEY = "doc:{doc_id}:parse_cancel"
_PARSE_TASK_TTL = 7200  # 2 hours
_PARSE_CANCEL_TTL = 60  # 1 minute

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_user_async)]  # Apply auth to all routes in this controller
)


async def _delete_file_record_async(
        db: AsyncSession,
        file_id: uuid.UUID,
        storage_service: FileStorageService,
) -> None:
    db_file = await db.get(file_model.File, file_id)
    if not db_file:
        api_logger.info(
            "File record already absent during document cleanup: file_id=%s",
            str(file_id),
        )
        return

    if db_file.file_ext == "folder":
        result = await db.execute(
            select(file_model.File).where(file_model.File.parent_id == db_file.id)
        )
        child_files = list(result.scalars().all())
        for child in child_files:
            if child.file_key:
                try:
                    await storage_service.delete_file(child.file_key)
                except Exception as e:
                    api_logger.warning(f"Failed to delete child file from storage: {child.file_key} - {e}")
            await db.delete(child)
    else:
        if db_file.file_key:
            try:
                await storage_service.delete_file(db_file.file_key)
            except Exception as e:
                api_logger.warning(f"Failed to delete file from storage: {db_file.file_key} - {e}")

    await db.delete(db_file)
    await db.commit()


@router.get("/{kb_id}/documents", response_model=ApiResponse)
async def get_documents(
        kb_id: uuid.UUID,
        parent_id: Optional[uuid.UUID] = Query(None, description="parent folder id when type is Folder"),
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (file name)"),
        document_ids: Optional[str] = Query(None, description="document ids, separated by commas"),
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Paged query document list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    api_logger.info(f"Query document list: kb_id={kb_id}, page={page}, pagesize={pagesize}, keywords={keywords}, document_ids={document_ids}, username: {current_user.username}")
    # 1. parameter validation
    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Construct query conditions
    filters = [
        document_model.Document.kb_id == kb_id,
        document_model.Document.status == 1
    ]

    if parent_id:
        file_result = await db.execute(
            select(file_model.File).where(file_model.File.parent_id == parent_id)
        )
        files = list(file_result.scalars().all())
        files_ids = [item.id for item in files]
        filters.append(document_model.Document.file_id.in_(files_ids))

    # Keyword search (fuzzy matching of file name)
    if keywords:
        api_logger.debug(f"Add keyword search criteria: {keywords}")
        filters.append(document_model.Document.file_name.ilike(f"%{keywords}%"))
    # document ids
    if document_ids:
        filters.append(document_model.Document.id.in_(document_ids.split(',')))

    # 3. Execute paged query
    try:
        api_logger.debug("Start executing document paging query")
        total, items = await document_service.get_documents_paginated_async(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            current_user=current_user
        )
        api_logger.info(f"Document query successful: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"Document query failed: {str(e)}")
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
            "has_next": True if page * pagesize < total else False
        }
    }
    return success(data=jsonable_encoder(result), msg="Query of document list succeeded")


@router.post("/document", response_model=ApiResponse)
async def create_document(
        create_data: document_schema.DocumentCreate,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    create document
    """
    api_logger.info(f"Create document request: file_name={create_data.file_name}, kb_id={create_data.kb_id}, username: {current_user.username}")

    try:
        api_logger.debug(f"Start creating a document: {create_data.file_name}")
        db_document = await document_service.create_document_async(db=db, document=create_data, current_user=current_user)
        api_logger.info(f"Document created successfully: {db_document.file_name} (ID: {db_document.id})")
        return success(data=jsonable_encoder(document_schema.Document.model_validate(db_document)), msg="Document creation successful")
    except Exception as e:
        api_logger.error(f"Document creation failed: {create_data.file_name} - {str(e)}")
        raise


@router.get("/{document_id}", response_model=ApiResponse)
async def get_document(
        document_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Retrieve document information based on document_id
    """
    api_logger.info(f"Obtain document information: document_id={document_id}, username: {current_user.username}")

    try:
        # 1. Query document information from the database
        api_logger.debug(f"query documentation: {document_id}")
        db_document = await document_service.get_document_by_id_async(db, document_id=document_id, current_user=current_user)
        if not db_document:
            api_logger.warning(f"The document does not exist or you do not have access: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The document does not exist or you do not have access"
            )

        api_logger.info(f"Document query successful: {db_document.file_name} (ID: {db_document.id})")
        return success(data=jsonable_encoder(document_schema.Document.model_validate(db_document)), msg="Successfully obtained document information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Document query failed: document_id={document_id} - {str(e)}")
        raise


@router.put("/{document_id}", response_model=ApiResponse)
async def update_document(
        document_id: uuid.UUID,
        update_data: document_schema.DocumentUpdate,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    Update document information
    """
    # 1. Check if the document exists
    api_logger.debug(f"Query the document to be updated: {document_id}")
    db_document = await document_service.get_document_by_id_async(db, document_id=document_id, current_user=current_user)

    if not db_document:
        api_logger.warning(f"The document does not exist or you do not have permission to access it: document_id={document_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=db_document.kb_id, current_user=current_user)

    # 2. 校验并处理 parser_config 更新
    update_dict = update_data.dict(exclude_unset=True)
    if "parser_config" in update_dict:
        new_config = update_dict["parser_config"]
        # 与 Document.is_parent_child_mode 保持一致的计算逻辑
        if "parent_child_mode" in new_config:
            new_mode_is_parent = new_config["parent_child_mode"]
        else:
            new_mode_is_parent = new_config.get("parent_chunk_mode", None) in ["paragraph", "full-doc"]
        kb_mode = db_knowledge.chunk_mode
        if kb_mode == 0:
            # 知识库未设置分块模式：首次设定，同步到知识库
            db_knowledge.parser_config.update(new_config)
            flag_modified(db_knowledge, "parser_config")
        elif (kb_mode == 1 and new_mode_is_parent) or (kb_mode == 2 and not new_mode_is_parent):
            # 已锁定且不一致：拒绝
            api_logger.warning(
                f"Document chunk mode deviates from knowledge base config: "
                f"document_id={document_id}, kb_mode={kb_mode}, new_mode={new_mode_is_parent}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="禁止变更分块模式"
            )
        # kb_mode=1 且 new=False，或 kb_mode=2 且 new=True：通过，不改知识库

    # 3.1 If updating the status, synchronize the document status switch to whether it can be retrieved from the vector database
    status_changed = False
    if "status" in update_dict:
        new_status = update_dict["status"]
        if new_status != db_document.status:
            status_changed = True
            await asyncio.to_thread(
                ElasticSearchVectorIndexOps.for_knowledge(db_knowledge.id).change_document_status,
                document_id=str(document_id),
                status=new_status,
            )

    # 3.2 Update fields (only update non-null fields)
    api_logger.debug(f"Start updating the document fields: {document_id}")
    updated_fields = []
    for field, value in update_dict.items():
        if hasattr(db_document, field):
            old_value = getattr(db_document, field)
            if old_value != value:
                # update value
                setattr(db_document, field, value)
                updated_fields.append(f"{field}: {old_value} -> {value}")

    if updated_fields:
        api_logger.debug(f"updated fields: {', '.join(updated_fields)}")

    db_document.updated_at = utcnow_naive()
    graph_parser_config = copy.deepcopy(db_knowledge.parser_config or {})
    graph_knowledge_id = str(db_knowledge.id)

    # 4. Save to database
    try:
        await db.commit()
        await db.refresh(db_document)
        api_logger.info(f"The document has been successfully updated: {db_document.file_name} (ID: {db_document.id})")
    except Exception as e:
        await db.rollback()
        api_logger.error(f"Document update failed: document_id={document_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document update failed: {str(e)}"
        )

    if status_changed:
        dispatch_document_graph_sync(
            graph_knowledge_id,
            str(document_id),
            graph_parser_config,
            dispatch_legacy=False,
        )

    # 5. Return the updated document
    return success(data=jsonable_encoder(document_schema.Document.model_validate(db_document)), msg="Document information updated successfully")


@router.delete("/{document_id}", response_model=ApiResponse)
async def delete_document(
        document_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    Delete document
    """
    api_logger.info(f"Request to delete document: document_id={document_id}, username: {current_user.username}")

    try:
        # 1. Check if the document exists
        api_logger.debug(f"Check whether the document exists: {document_id}")
        db_document = await document_service.get_document_by_id_async(db, document_id=document_id, current_user=current_user)

        if not db_document:
            api_logger.warning(f"The document does not exist or you do not have permission to access it: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The document does not exist or you do not have permission to access it"
            )
        file_id = db_document.file_id
        kb_id = db_document.kb_id

        db_knowledge = await knowledge_service.get_knowledge_by_id_async(
            db,
            knowledge_id=kb_id,
            current_user=current_user,
        )
        graph_parser_config = copy.deepcopy(db_knowledge.parser_config or {})
        graph_knowledge_id = str(db_knowledge.id)
        document_file_name = db_document.file_name

        # 2. Cancel any running parse task via Redis
        task_id = REDIS_CONN.get(_PARSE_TASK_KEY.format(doc_id=document_id))
        if task_id:
            api_logger.warning(f"[DELETE] Revoking running parse task: task_id={task_id}, document_id={document_id}")
            try:
                celery_app.control.revoke(task_id)
                api_logger.warning(f"[DELETE] Revoke signal sent for task_id={task_id}")
            except NotImplementedError:
                # ThreadPool does not support terminate; rely on Redis cancel marker
                api_logger.info(f"[DELETE] ThreadPool does not support terminate, relying on Redis cancel marker for task_id={task_id}")
            except Exception as revoke_err:
                api_logger.error(f"[DELETE] Failed to revoke task {task_id}: {revoke_err}")
        # Set cancellation marker and clean up task key
        REDIS_CONN.set(_PARSE_CANCEL_KEY.format(doc_id=document_id), "1", exp=_PARSE_CANCEL_TTL)
        REDIS_CONN.delete(_PARSE_TASK_KEY.format(doc_id=document_id))

        # 3. Delete vector index (non-404 failures raise, caught by except below)
        await asyncio.to_thread(
            ElasticSearchVectorIndexOps.for_knowledge(db_knowledge.id).delete_by_metadata_field,
            key="document_id",
            value=str(document_id),
        )

        # 4. Delete file (storage errors are swallowed internally)
        await _delete_file_record_async(db=db, file_id=file_id, storage_service=storage_service)

        # 5. Delete metadata bindings (app-level cleanup, FK no longer has CASCADE)
        api_logger.debug(f"Delete metadata bindings for document: {document_id}")
        await KnowledgeMetadataService.delete_document_metadata_async(db, document_id)

        # 6. Persist graph cleanup before hard-deleting the document record.
        await enqueue_document_graph_sync(
            graph_knowledge_id,
            str(document_id),
            graph_parser_config,
            dispatch_legacy=False,
            document_deleted=True,
        )

        # 7. Delete document from DB only after graph cleanup is recoverable.
        api_logger.debug(f"Perform document delete: {db_document.file_name} (ID: {document_id})")
        await db.delete(db_document)
        await db.commit()

        api_logger.info(f"The document has been successfully deleted: {document_file_name} (ID: {document_id})")
        return success(msg="The document has been successfully deleted")
    except Exception as e:
        api_logger.error(
            "Failed to delete document"
            " document_id=%s error_type=%s",
            str(document_id),
            type(e).__name__,
        )
        raise


@router.post("/{document_id}/chunks", response_model=ApiResponse)
async def parse_documents(
        document_id: uuid.UUID,
        db: AsyncSession = Depends(get_async_db),
        current_user: User = Depends(get_current_user_async)
):
    """
    parse document
    """
    api_logger.info(f"Request to parse document: document_id={document_id}, username: {current_user.username}")

    try:
        # 1. Check if the document exists
        api_logger.debug(f"Check whether the document exists: {document_id}")
        db_document = await document_service.get_document_by_id_async(db, document_id=document_id, current_user=current_user)

        if not db_document:
            api_logger.warning(f"The document does not exist or you do not have permission to access it: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The document does not exist or you do not have permission to access it"
            )

        # 2. Check if the file exists
        api_logger.debug(f"Check whether the file exists: {db_document.file_id}")
        db_file = await db.get(file_model.File, db_document.file_id)

        if not db_file:
            api_logger.warning(f"The file does not exist or you do not have permission to access it: file_id={db_document.file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The file does not exist or you do not have permission to access it"
            )

        # 3. Get file_key for storage backend
        if not db_file.file_key:
            api_logger.error(f"File has no storage key (legacy data not migrated): file_id={db_file.id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File has no storage key (legacy data not migrated)"
            )

        # 4. Atomically claim parse slot via Redis SET NX
        redis_client = REDIS_CONN.REDIS
        task_key = _PARSE_TASK_KEY.format(doc_id=document_id)
        claimed = redis_client.set(task_key, "CLAIMED", ex=_PARSE_TASK_TTL, nx=True)
        if not claimed:
            existing_task_id = REDIS_CONN.get(task_key)
            api_logger.info(f"Document is already being parsed: document_id={document_id}, task_id={existing_task_id}")
            return success(data={"task_id": existing_task_id or "unknown"}, msg="Document is already being parsed.")

        # 5. Obtain knowledge base information
        api_logger.info(f"Obtain details of the knowledge base: knowledge_id={db_document.kb_id}")
        db_knowledge = await knowledge_service.get_knowledge_by_id_async(db, knowledge_id=db_document.kb_id, current_user=current_user)
        if not db_knowledge:
            # Rollback Redis claim on failure
            REDIS_CONN.delete(task_key)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")

        # 6. Dispatch parse task with file_key (not file_path)
        try:
            task = celery_app.send_task(
                "app.core.rag.tasks.parse_document",
                args=[db_file.file_key, document_id, db_file.file_name]
            )
        except Exception:
            # Rollback Redis claim if Celery dispatch fails
            REDIS_CONN.delete(task_key)
            raise

        # 7. Store real task_id in Redis (overwrite CLAIMED)
        REDIS_CONN.set(task_key, task.id, exp=_PARSE_TASK_TTL)

        result = {
            "task_id": task.id
        }
        return success(data=result, msg="Task accepted. The document is being processed in the background.")
    except Exception as e:
        api_logger.error(f"Failed to parse document: document_id={document_id} - {str(e)}")
        raise


@router.post("/metadata/batch", response_model=ApiResponse)
async def batch_update_document_metadata(
    data: metadata_schema.BatchUpdateMetadataRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    """
    批量更新文档元数据
    - 所有文档必须属于同一知识库且当前用户有权限访问
    - 事务性：全成功或全回滚
    """
    api_logger.info(
        f"Batch update document metadata: count={len(data.items)}, user={current_user.username}"
    )

    # 1. 校验所有文档的权限和归属（必须属于当前 workspace）
    for item in data.items:
        db_document = await require_current_workspace_document_async(
            db=db,
            document_id=item.document_id,
            current_user=current_user,
        )
        if not db_document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"文档不存在或无权访问: {item.document_id}"
            )

    items = [
        {
            "document_id": item.document_id,
            "metadata": item.metadata,
        }
        for item in data.items
    ]

    result = await KnowledgeMetadataService.batch_update_document_metadata_async(
        db=db,
        items=items,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
    )

    return success(data=result, msg="批量更新完成")


@router.put("/{document_id}/metadata", response_model=ApiResponse)
async def update_document_metadata(
    document_id: uuid.UUID,
    data: metadata_schema.DocumentMetadataUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    """
    更新单个文档的元数据
    - 字段必须在知识库中已定义
    - 值类型必须与字段定义一致
    """
    api_logger.info(
        f"Update document metadata: document_id={document_id}, user={current_user.username}"
    )

    # 1. 校验文档存在且有权访问
    db_document = await require_current_workspace_document_async(
        db=db,
        document_id=document_id,
        current_user=current_user
    )
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    # 2. 调用 Service 更新
    result = await KnowledgeMetadataService.update_document_metadata_async(
        db=db,
        document_id=document_id,
        metadata=data.metadata,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
    )

    return success(
        data=result,
        msg="文档元数据更新成功",
    )


@router.get("/{document_id}/metadata", response_model=ApiResponse)
async def get_document_metadata(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    """获取单个文档的元数据"""
    api_logger.info(
        f"Get document metadata: document_id={document_id}, user={current_user.username}"
    )

    # 校验文档存在且有权访问
    db_document = await require_current_workspace_document_async(
        db=db,
        document_id=document_id,
        current_user=current_user
    )
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    result = await KnowledgeMetadataService.get_document_metadata_async(
        db=db,
        document_id=document_id,
    )

    return success(
        data=result,
        msg="获取文档元数据成功",
    )


@router.post("/{document_id}/metadata", response_model=ApiResponse)
async def delete_document_metadata(
    document_id: uuid.UUID,
    data: metadata_schema.DocumentMetadataDeleteRequest | None = Body(None),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user_async),
):
    """
    删除单个文档的元数据
    - 不传 body 或 field_names 为空时，清空全部元数据
    - 传 field_names 时，仅删除指定字段
    """
    api_logger.info(
        f"Delete document metadata: document_id={document_id}, user={current_user.username}"
    )

    # 校验文档存在且有权访问
    db_document = await require_current_workspace_document_async(
        db=db,
        document_id=document_id,
        current_user=current_user
    )
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    field_names = data.field_names if data else None

    result = await KnowledgeMetadataService.delete_document_metadata_async(
        db=db,
        document_id=document_id,
        field_names=field_names,
    )

    return success(
        data=result,
        msg="文档元数据删除成功",
    )
