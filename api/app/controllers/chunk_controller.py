import os
import csv
import io
from typing import Any, Optional
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.rag.common.settings import kg_retriever
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.cv_model import QWenCV
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.rag.metadata.filter_engine import MetadataFilterEngine, FilterCondition, FilterGroup
from app.services.knowledge_metadata_service import KnowledgeMetadataService
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models import knowledge_model, knowledgeshare_model
from app.models.document_model import Document
from app.models.user_model import User
from app.schemas import chunk_schema
from app.schemas.response_schema import ApiResponse
from app.services import knowledge_service, document_service, file_service, knowledgeshare_service
from app.services.file_storage_service import FileStorageService, get_file_storage_service, generate_kb_file_key
from app.services.model_service import ModelApiKeyService
from app.core.rag.utils.preview_utils import _build_preview_hierarchy
from app.core.utils.datetime_utils import to_timestamp_ms

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/chunks",
    tags=["chunks"],
    dependencies=[Depends(get_current_user)]  # Apply auth to all routes in this controller
)


@router.get("/{kb_id}/{document_id}/previewchunks", response_model=ApiResponse)
async def get_preview_chunks(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        keywords: Optional[str] = Query(None, description="The keywords used to match chunk content"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Paged query document block preview list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - Return paging metadata + file list
    """
    api_logger.info(f"Paged query document block preview list: kb_id={kb_id}, document_id={document_id}, page={page}, pagesize={pagesize}, keywords={keywords}, username: {current_user.username}")
    # 1. parameter validation
    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Obtain knowledge base information
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )
    # 3. Check if the document exists
    db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    # 4. Check if the file exists
    db_file = file_service.get_file_by_id(db, file_id=db_document.file_id)
    if not db_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist or you do not have permission to access it"
        )

    # 5. Get file content from storage backend
    if not db_file.file_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File has no storage key (legacy data not migrated)"
        )

    from app.services.file_storage_service import FileStorageService
    storage_service = FileStorageService()

    try:
        file_binary = await storage_service.download_file(db_file.file_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found in storage: {e}"
        )

    # 7. Document parsing & segmentation
    def progress_callback(prog=None, msg=None):
        print(f"prog: {prog} msg: {msg}\n")
    # Prepare to configure vision_model information
    vision_model = QWenCV(
            key=db_knowledge.image2text.api_keys[0].api_key,
            model_name=db_knowledge.image2text.api_keys[0].model_name,
            lang="Chinese",
            base_url=db_knowledge.image2text.api_keys[0].api_base
        )
    from app.core.rag.app.naive import chunk
    parent_child_mode = db_document.is_parent_child_mode
    api_logger.debug(f"当前文档分块模式：{db_document.is_parent_child_mode}")
    if parent_child_mode:
        from app.core.rag.app.naive import chunk_parent_child
        child_res, parent_res, parent_id_map = chunk_parent_child(
            filename=db_file.file_name,
            binary=file_binary,
            from_page=0,
            to_page=5,
            callback=progress_callback,
            vision_model=vision_model,
            parser_config=db_document.parser_config,
            is_root=False,
        )
        # Combine parent and child chunks for preview
        parent_id_to_doc_id = {}
        all_preview = []
        for idx, item in enumerate(parent_res):
            pid = uuid.uuid4().hex
            parent_id_to_doc_id[idx] = pid
            meta = {
                "doc_id": pid,
                "file_id": str(db_document.file_id),
                "file_name": db_document.file_name,
                "file_created_at": to_timestamp_ms(db_document.created_at),
                "document_id": str(db_document.id),
                "knowledge_id": str(db_document.kb_id),
                "sort_id": idx,
                "status": 1,
                "chunk_type": "parent",
            }
            all_preview.append(DocumentChunk(page_content=item["content_with_weight"], metadata=meta))
        for idx, item in enumerate(child_res):
            parent_idx = parent_id_map.get(idx)
            meta = {
                "doc_id": uuid.uuid4().hex,
                "file_id": str(db_document.file_id),
                "file_name": db_document.file_name,
                "file_created_at": to_timestamp_ms(db_document.created_at),
                "document_id": str(db_document.id),
                "knowledge_id": str(db_document.kb_id),
                "sort_id": idx,
                "status": 1,
                "chunk_type": "child",
                "parent_id": parent_id_to_doc_id.get(parent_idx, ""),
            }
            all_preview.append(DocumentChunk(page_content=item["content_with_weight"], metadata=meta))
        res = all_preview
    else:
        res = chunk(filename=db_file.file_name,
                    binary=file_binary,
                    from_page=0,
                    to_page=5,
                    callback=progress_callback,
                    vision_model=vision_model,
                    parser_config=db_document.parser_config,
                    is_root=False)

    start_index = (page - 1) * pagesize
    end_index = start_index + pagesize
    # Use slicing to obtain the data of the current page
    paginated_chunk_str_list = res[start_index:end_index]
    chunks = []
    for idx, item in enumerate(paginated_chunk_str_list):
        if parent_child_mode:
            # item is already a DocumentChunk in parent-child mode
            chunks.append(item)
        else:
            metadata = {
                "doc_id": uuid.uuid4().hex,
                "file_id": str(db_document.file_id),
                "file_name": db_document.file_name,
                "file_created_at": to_timestamp_ms(db_document.created_at),
                "document_id": str(db_document.id),
                "knowledge_id": str(db_document.kb_id),
                "sort_id": idx,
                "status": 1,
            }
            chunks.append(DocumentChunk(page_content=item["content_with_weight"], metadata=metadata))

    # 8. Return structured response
    total = len(res)
    result = {
        "items": chunks,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page * pagesize < total else False
        }
    }
    api_logger.info(f"Querying the document block preview list successful: total={total}, returned={len(chunks)} records")
    return success(data=jsonable_encoder(result), msg="Querying the document block preview list succeeded")


@router.post("/{kb_id}/{document_id}/preview", response_model=ApiResponse)
async def get_preview_chunks_hierarchy(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        page: int = Query(1, gt=0),
        pagesize: int = Query(20, gt=0, le=100),
        keywords: Optional[str] = Query(None, description="The keywords used to match chunk content"),
        parser_config_param: Optional[dict] = Body(None, description="Parser config overrides, e.g. {\"layout_recognize\":\"mineru\",\"chunk_token_num\":130,\"parent_child_mode\":true,\"parent_chunk_mode\":\"full-doc\"}"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Paged query document chunk preview (nested structure)
    - Supports three modes: normal chunk, parent-child chunk, and QA chunk
    - Returns nested DocumentChunk structure, children field contains sub-chunks
    - Pagination slices at the parent chunk (top-level chunk) level
    """
    api_logger.info(f"Paged query document chunk preview hierarchy: kb_id={kb_id}, document_id={document_id}, page={page}, pagesize={pagesize}")

    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    db_file = file_service.get_file_by_id(db, file_id=db_document.file_id)
    if not db_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist or you do not have permission to access it"
        )

    if not db_file.file_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File has no storage key (legacy data not migrated)"
        )

    from app.services.file_storage_service import FileStorageService
    storage_service = FileStorageService()

    try:
        file_binary = await storage_service.download_file(db_file.file_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found in storage: {e}"
        )

    def progress_callback(prog=None, msg=None):
        print(f"prog: {prog} msg: {msg}\n")

    vision_model = QWenCV(
        key=db_knowledge.image2text.api_keys[0].api_key,
        model_name=db_knowledge.image2text.api_keys[0].model_name,
        lang="Chinese",
        base_url=db_knowledge.image2text.api_keys[0].api_base
    )
    from app.core.rag.app.naive import chunk, chunk_parent_child

    parser_config = dict(db_document.parser_config)

    if parser_config_param and isinstance(parser_config_param, dict):
        # 兼容 {"parser_config": {...}} 和直接 {...} 两种传法
        actual_config = parser_config_param.get("parser_config", parser_config_param)
        if isinstance(actual_config, dict):
            parser_config.update(actual_config)

    chunk_mode = parser_config.get("chunk_mode", "normal")
    parent_child_mode = parser_config.get("parent_child_mode", False)

    if parent_child_mode:
        chunk_mode = "parent_child"

    try:
        if chunk_mode == "parent_child":
            child_res, parent_res, parent_id_map = chunk_parent_child(
                filename=db_file.file_name,
                binary=file_binary,
                from_page=0,
                to_page=5,
                callback=progress_callback,
                vision_model=vision_model,
                parser_config=parser_config,
                is_root=False,
            )
            hierarchy = _build_preview_hierarchy(
                child_res,
                chunk_mode="parent_child",
                parent_chunks=parent_res,
                parent_id_map=parent_id_map,
            )
        elif chunk_mode == "qa":
            res = chunk(
                filename=db_file.file_name,
                binary=file_binary,
                from_page=0,
                to_page=5,
                callback=progress_callback,
                vision_model=vision_model,
                parser_config=parser_config,
                is_root=False,
            )
            hierarchy = _build_preview_hierarchy(res, chunk_mode="qa")
        else:
            res = chunk(
                filename=db_file.file_name,
                binary=file_binary,
                from_page=0,
                to_page=5,
                callback=progress_callback,
                vision_model=vision_model,
                parser_config=parser_config,
                is_root=False,
            )
            hierarchy = _build_preview_hierarchy(res, chunk_mode="normal")
    except Exception as e:
        api_logger.error(f"Document parsing failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document parsing failed: {str(e)}"
        )

    total = len(hierarchy)
    start_index = (page - 1) * pagesize
    end_index = start_index + pagesize
    paginated = hierarchy[start_index:end_index]

    result = {
        "items": paginated,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": page * pagesize < total
        }
    }
    api_logger.info(f"Querying document chunk preview hierarchy succeeded: total={total}, returned={len(paginated)}")
    return success(data=jsonable_encoder(result), msg="Querying document chunk preview hierarchy succeeded")


@router.get("/{kb_id}/{document_id}/chunks", response_model=ApiResponse)
async def get_chunks(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        keywords: Optional[str] = Query(None, description="The keywords used to match chunk content"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Paged query document chunk list
    - Support filtering by document_id
    - Support keyword search for segmented content
    - For parent-child mode: return nested structure (parent chunks with children)
    - For normal mode: return flat chunk list
    """
    api_logger.info(f"Paged query document chunk list: kb_id={kb_id}, document_id={document_id}, page={page}, pagesize={pagesize}, keywords={keywords}, username: {current_user.username}")
    # 1. parameter validation
    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Obtain knowledge base information
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    # 3. 获取文档并判断分块模式
    db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    def _build_nested_result(
        parents: list[DocumentChunk],
        children: list[DocumentChunk],
        page_num: int,
        page_size: int,
        total: int,
    ) -> dict:
        """将 parent chunks 和 child chunks 组装为嵌套结构并返回分页结果."""
        children_map: dict[str, list[DocumentChunk]] = {}
        for child in children:
            pid = child.metadata.get("parent_id")
            if pid:
                children_map.setdefault(pid, []).append(child)
        nested = []
        for parent in parents:
            parent.children = children_map.get(parent.metadata.get("doc_id"), [])
            nested.append(parent)
        return {
            "items": nested,
            "page": {
                "page": page_num,
                "pagesize": page_size,
                "total": total,
                "has_next": page_num * page_size < total,
            },
        }

    # 4. Execute paged query
    try:
        api_logger.debug("Start executing document chunk query")
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        if db_document.is_parent_child_mode:
            # 方案 1：两次查询 + parent 级分页
            # 4.1 查询 parent chunks（按 sort_id 排序，分页）
            total_parents, parent_items = vector_service.search_by_segment(
                document_id=str(document_id),
                query=keywords,
                pagesize=pagesize,
                page=page,
                asc=True,
                chunk_types="parent",
            )

            # fallback：如果 parent 查询为空（旧数据或 chunk_type 缺失），查所有 chunks 在内存中区分
            if not parent_items and total_parents == 0:
                api_logger.debug("Parent query returned empty, falling back to query all chunks")
                total_all, all_items = vector_service.search_by_segment(
                    document_id=str(document_id),
                    query=keywords,
                    pagesize=10000,
                    page=1,
                    asc=True,
                )
                parent_items = [item for item in all_items if (item.metadata or {}).get("chunk_type") == "parent"]
                child_items_fallback = [item for item in all_items if (item.metadata or {}).get("chunk_type") == "child"]
                total_parents = len(parent_items)

                if not parent_items:
                    # 仍然没有 parent，按普通分块模式返回
                    result = {
                        "items": all_items[(page - 1) * pagesize : page * pagesize],
                        "page": {
                            "page": page,
                            "pagesize": pagesize,
                            "total": total_all,
                            "has_next": page * pagesize < total_all,
                        },
                    }
                    return success(data=jsonable_encoder(result), msg="Query of document chunk list succeeded")

                # 内存分页 + 组装
                paginated_parents = parent_items[(page - 1) * pagesize : page * pagesize]
                result = _build_nested_result(
                    paginated_parents, child_items_fallback, page, pagesize, total_parents
                )
                return success(data=jsonable_encoder(result), msg="Query of document chunk list succeeded")

            parent_doc_ids = [p.metadata["doc_id"] for p in parent_items]

            # 4.2 查询这些 parent 下的所有 child chunks（按 sort_id 排序，不分页）
            _, child_items = vector_service.search_by_segment(
                document_id=str(document_id),
                pagesize=10000,
                page=1,
                asc=True,
                chunk_types="child",
                parent_ids=parent_doc_ids,
            )

            # 4.3 组装嵌套结构
            result = _build_nested_result(parent_items, child_items, page, pagesize, total_parents)
        else:
            # 普通分块模式：原有逻辑
            total, items = vector_service.search_by_segment(
                document_id=str(document_id),
                query=keywords,
                pagesize=pagesize,
                page=page,
                asc=True
            )
            result = {
                "items": items,
                "page": {
                    "page": page,
                    "pagesize": pagesize,
                    "total": total,
                    "has_next": page * pagesize < total
                }
            }

        api_logger.info(f"Document chunk query successful: returned={len(result['items'])} records")
    except Exception as e:
        api_logger.error(f"Document chunk query failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )

    return success(data=jsonable_encoder(result), msg="Query of document chunk list succeeded")


@router.post("/{kb_id}/{document_id}/chunk", response_model=ApiResponse)
async def create_chunk(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        create_data: chunk_schema.ChunkCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    create chunk
    """
    # Obtain the actual content
    content = create_data.chunk_content
    api_logger.info(f"Create chunk request: kb_id={kb_id}, document_id={document_id}, content={content}, username: {current_user.username}")

    # 1. Obtain knowledge base information
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )
    # 1. Obtain document information
    db_document = db.query(Document).filter(Document.id == document_id).first()
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    # 校验 chunk_type 与文档分块模式的一致性
    if db_document.is_parent_child_mode:
        if create_data.chunk_type not in (chunk_schema.ChunkType.PARENT, chunk_schema.ChunkType.CHILD):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="父子分块模式下仅允许创建 parent 或 child 类型块"
            )
        if create_data.chunk_type == chunk_schema.ChunkType.CHILD and not create_data.parent_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="创建子块时必须提供 parent_id"
            )
    else:
        if create_data.chunk_type in (chunk_schema.ChunkType.PARENT, chunk_schema.ChunkType.CHILD):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前文档未启用父子分块模式，不允许创建 parent/child 类型块"
            )

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

    # 2. Get the sort ID
    sort_id = 0
    total, items = vector_service.search_by_segment(document_id=str(document_id), pagesize=1, page=1, asc=False)
    if items:
        sort_id = items[0].metadata["sort_id"]
    sort_id = sort_id + 1

    doc_id = uuid.uuid4().hex
    metadata = {
        "doc_id": doc_id,
        "file_id": str(db_document.file_id),
        "file_name": db_document.file_name,
        "file_created_at": to_timestamp_ms(db_document.created_at),
        "document_id": str(document_id),
        "knowledge_id": str(kb_id),
        "sort_id": sort_id,
        "status": 1,
        **create_data.type_metadata,
    }
    # QA chunk: 注入 question/answer 到 metadata
    if create_data.is_qa:
        metadata.update(create_data.qa_metadata)
    chunk = DocumentChunk(page_content=content, metadata=metadata)
    # 3. Segmented vector storage
    vector_service.add_chunks([chunk])

    # 4.update chunk_num
    db_document.chunk_num += 1
    db.commit()

    return success(data=jsonable_encoder(chunk), msg="Document chunk creation successful")


@router.post("/{kb_id}/{document_id}/chunk/batch", response_model=ApiResponse)
async def create_chunks_batch(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        batch_data: chunk_schema.ChunkBatchCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Batch create chunks (max 8)
    """
    api_logger.info(f"Batch create chunks: kb_id={kb_id}, document_id={document_id}, count={len(batch_data.items)}, username: {current_user.username}")

    if len(batch_data.items) > settings.MAX_CHUNK_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch size exceeds limit: max {settings.MAX_CHUNK_BATCH_SIZE}, got {len(batch_data.items)}"
        )

    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The knowledge base does not exist or access is denied")

    db_document = db.query(Document).filter(
        Document.id == document_id,
        Document.kb_id == kb_id
    ).first()
    if not db_document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The document does not exist or you do not have permission to access it")

    # 批量校验 chunk_type
    if db_document.is_parent_child_mode:
        for item in batch_data.items:
            if item.chunk_type not in (chunk_schema.ChunkType.PARENT, chunk_schema.ChunkType.CHILD):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="父子分块模式下仅允许创建 parent 或 child 类型块"
                )
            if item.chunk_type == chunk_schema.ChunkType.CHILD and not item.parent_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="创建子块时必须提供 parent_id"
                )
    else:
        for item in batch_data.items:
            if item.chunk_type in (chunk_schema.ChunkType.PARENT, chunk_schema.ChunkType.CHILD):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="当前文档未启用父子分块模式，不允许创建 parent/child 类型块"
                )

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

    # Get current max sort_id
    sort_id = 0
    total, items = vector_service.search_by_segment(document_id=str(document_id), pagesize=1, page=1, asc=False)
    if items:
        sort_id = items[0].metadata["sort_id"]

    chunks = []
    for create_data in batch_data.items:
        sort_id += 1
        doc_id = uuid.uuid4().hex
        metadata = {
            "doc_id": doc_id,
            "file_id": str(db_document.file_id),
            "file_name": db_document.file_name,
            "file_created_at": to_timestamp_ms(db_document.created_at),
            "document_id": str(document_id),
            "knowledge_id": str(kb_id),
            "sort_id": sort_id,
            "status": 1,
            **create_data.type_metadata,
        }
        if create_data.is_qa:
            metadata.update(create_data.qa_metadata)
        chunks.append(DocumentChunk(page_content=create_data.chunk_content, metadata=metadata))

    vector_service.add_chunks(chunks)

    db_document.chunk_num += len(chunks)
    db.commit()

    return success(data=jsonable_encoder(chunks), msg=f"Batch created {len(chunks)} chunks successfully")


@router.post("/{kb_id}/import_qa", response_model=ApiResponse)
async def import_qa_new_doc(
        kb_id: uuid.UUID,
        file: UploadFile = File(..., description="CSV 或 Excel 文件（第一行标题跳过，第一列问题，第二列答案）"),
        parent_id: Optional[uuid.UUID] = Query(None, description="parent folder id"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    导入 QA 问答对并新建文档（CSV/Excel），异步处理
    """
    from app.schemas import file_schema, document_schema

    api_logger.info(f"Import QA (new doc): kb_id={kb_id}, file={file.filename}, username: {current_user.username}")

    # 1. 校验文件格式
    filename = file.filename or ""
    if not (filename.endswith(".csv") or filename.endswith(".xlsx") or filename.endswith(".xls")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 CSV (.csv) 或 Excel (.xlsx) 格式")

    # 2. 校验知识库
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在或无权访问")

    # 3. 读取文件
    contents = await file.read()
    file_size = len(contents)
    if file_size == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件为空")

    _, file_extension = os.path.splitext(filename)
    file_ext = file_extension.lower()

    # 4. 创建 File 记录
    file_data = file_schema.FileCreate(
        kb_id=kb_id, created_by=current_user.id,
        parent_id=parent_id,
        file_name=filename, file_ext=file_ext, file_size=file_size,
    )
    db_file = file_service.create_file(db=db, file=file_data, current_user=current_user)

    # 5. 上传文件到存储后端
    file_key = generate_kb_file_key(kb_id=kb_id, file_id=db_file.id, file_ext=file_ext)
    try:
        await storage_service.storage.upload(file_key=file_key, content=contents, content_type=file.content_type)
    except Exception as e:
        api_logger.error(f"Storage upload failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"文件存储失败: {str(e)}")

    db_file.file_key = file_key
    db.commit()
    db.refresh(db_file)

    # 6. 创建 Document 记录（标记为 QA 类型）
    doc_data = document_schema.DocumentCreate(
        kb_id=kb_id, created_by=current_user.id, file_id=db_file.id,
        file_name=filename, file_ext=file_ext, file_size=file_size,
        file_meta={}, parser_id="qa",
        parser_config={"doc_type": "qa", "auto_questions": 0}
    )
    db_document = document_service.create_document(db=db, document=doc_data, current_user=current_user)

    api_logger.info(f"Created doc for QA import: file_id={db_file.id}, document_id={db_document.id}, file_key={file_key}")

    # 7. 派发异步任务
    from app.celery_app import celery_app
    task = celery_app.send_task(
        "app.core.rag.tasks.import_qa_chunks",
        args=[str(kb_id), str(db_document.id), filename, contents],
        queue="qa_import"
    )

    return success(data={
        "task_id": task.id,
        "document_id": str(db_document.id),
        "file_id": str(db_file.id),
    }, msg="QA 导入任务已提交，后台处理中")


@router.post("/{kb_id}/{document_id}/import_qa", response_model=ApiResponse)
async def import_qa_chunks(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        file: UploadFile = File(..., description="CSV 或 Excel 文件（第一行标题跳过，第一列问题，第二列答案）"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    导入 QA 问答对（CSV/Excel），异步处理
    """
    api_logger.info(f"Import QA chunks: kb_id={kb_id}, document_id={document_id}, file={file.filename}, username: {current_user.username}")

    # 1. 校验文件格式
    filename = file.filename or ""
    if not (filename.endswith(".csv") or filename.endswith(".xlsx") or filename.endswith(".xls")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 CSV (.csv) 或 Excel (.xlsx) 格式")

    # 2. 校验知识库和文档
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在或无权访问")

    db_document = db.query(Document).filter(Document.id == document_id).first()
    if not db_document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在或无权访问")

    # 3. 读取文件内容，派发异步任务
    contents = await file.read()

    from app.celery_app import celery_app
    task = celery_app.send_task(
        "app.core.rag.tasks.import_qa_chunks",
        args=[str(kb_id), str(document_id), filename, contents],
        queue="qa_import"
    )

    return success(data={"task_id": task.id}, msg="QA 导入任务已提交，后台处理中")


@router.get("/{kb_id}/{document_id}/{doc_id}", response_model=ApiResponse)
async def get_chunk(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        doc_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve document chunk information based on doc_id
    """
    api_logger.info(f"Obtain document chunk information: kb_id={kb_id}, document_id={document_id}, doc_id={doc_id}, username: {current_user.username}")

    # 1. Obtain knowledge base information
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
    total, items = vector_service.get_by_segment(doc_id=doc_id)
    if total:
        return success(data=jsonable_encoder(items[0]), msg="Document chunk query successful")
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document chunk does not exist or you do not have access"
        )


@router.put("/{kb_id}/{document_id}/{doc_id}", response_model=ApiResponse)
async def update_chunk(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        doc_id: str,
        update_data: chunk_schema.ChunkUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Update document chunk content
    """
    # Obtain the actual content
    content = update_data.chunk_content
    api_logger.info(f"Update document chunk content: kb_id={kb_id}, document_id={document_id}, doc_id={doc_id}, content={content}, username: {current_user.username}")

    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
    total, items = vector_service.get_by_segment(doc_id=doc_id)
    if total:
        chunk = items[0]
        chunk.page_content = content
        # QA chunk: 更新 metadata 中的 question/answer
        if update_data.is_qa:
            chunk.metadata.update(update_data.qa_metadata)
        vector_service.update_by_segment(chunk)
        return success(data=jsonable_encoder(chunk), msg="The document chunk has been successfully updated")
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document chunk does not exist or you do not have access to it"
        )


@router.delete("/{kb_id}/{document_id}/{doc_id}", response_model=ApiResponse)
async def delete_chunk(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        doc_id: str,
        force_refresh: bool = Query(False, description="Force Elasticsearch refresh after deletion"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    delete document chunk
    """
    api_logger.info(f"Request to delete document chunk: kb_id={kb_id}, document_id={document_id}, doc_id={doc_id}, username: {current_user.username}")

    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
    if vector_service.text_exists(doc_id):
        vector_service.delete_by_ids([doc_id], refresh=force_refresh)
        # 更新 chunk_num
        db_document = db.query(Document).filter(Document.id == document_id).first()
        db_document.chunk_num -= 1
        db.commit()
        return success(msg="The document chunk has been successfully deleted")
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document chunk does not exist or you do not have access to it"
        )


@router.get("/retrieve_type", response_model=ApiResponse)
def get_retrieve_types():
    return success(msg="Successfully obtained the retrieval type", data=list(chunk_schema.RetrieveType))


@router.post("/retrieval", response_model=Any, status_code=status.HTTP_200_OK)
async def retrieve_chunks(
        retrieve_data: chunk_schema.ChunkRetrieve,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    retrieve chunk
    """
    api_logger.info(f"retrieve chunk: query={retrieve_data.query}, username: {current_user.username}")

    # Resolve ex_ids to kb_ids and merge (union)
    kb_ids = list(retrieve_data.kb_ids)
    if retrieve_data.ex_ids:
        resolved_ids = knowledge_service.get_knowledge_ids_by_external_ids(
            db=db,
            external_ids=retrieve_data.ex_ids,
            workspace_id=current_user.current_workspace_id,
            current_user=current_user
        )
        kb_ids = list(set(kb_ids + resolved_ids))

    filters = [
        knowledge_model.Knowledge.id.in_(kb_ids),
        knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
        knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
        knowledge_model.Knowledge.chunk_num > 0,
        knowledge_model.Knowledge.status == 1
    ]
    private_items = knowledge_service.get_chunked_knowledgeids(
        db=db,
        filters=filters,
        current_user=current_user
    )
    private_kb_ids = [item[0] for item in private_items]
    private_workspace_ids = [item[1] for item in private_items]
    filters = [
        knowledge_model.Knowledge.id.in_(kb_ids),
        knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id,
        knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
        knowledge_model.Knowledge.chunk_num > 0,
        knowledge_model.Knowledge.status == 1
    ]
    items = knowledge_service.get_chunked_knowledgeids(
        db=db,
        filters=filters,
        current_user=current_user
    )
    if items:
        filters = [
            knowledgeshare_model.KnowledgeShare.target_kb_id.in_(kb_ids),
            knowledgeshare_model.KnowledgeShare.target_workspace_id == current_user.current_workspace_id,
        ]
        share_items = knowledgeshare_service.get_source_kb_ids_by_target_kb_id(
            db=db,
            filters=filters,
            current_user=current_user
        )
        share_kb_ids = [item[0] for item in share_items]
        share_workspace_ids = [item[1] for item in share_items]
        private_kb_ids.extend(share_kb_ids)
        private_workspace_ids.extend(share_workspace_ids)
    if not private_kb_ids:
        return success(data=[], msg="retrieval successful")
    kb_id = private_kb_ids[0]
    uuid_strs = [f"Vector_index_{kb_id}_Node".lower() for kb_id in private_kb_ids]
    indices = ",".join(uuid_strs)
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    # === 元数据过滤 ===
    document_ids_filter = None
    if retrieve_data.metadata_filters:
        # 1) auto 模式拒绝
        if retrieve_data.metadata_filter_mode.value != "manual":
            raise BusinessException(
                "metadata_filter_mode 暂仅支持 'manual'",
                code=BizCode.INVALID_PARAMETER,
            )

        # 2) 收集所有库的字段定义
        all_metadata_defs: dict[uuid.UUID, dict[str, dict]] = {}
        for pk_kb_id in private_kb_ids:
            all_metadata_defs[pk_kb_id] = KnowledgeMetadataService.get_metadata_defs_for_filtering(db, pk_kb_id)

        # 3) 找出公共字段（所有库都有 + 类型一致）
        # 先取所有字段名的交集
        all_field_names = set()
        for defs in all_metadata_defs.values():
            all_field_names.update(defs.keys())

        common_fields = set()
        for field_name in all_field_names:
            field_types = set()
            all_have = True
            for defs in all_metadata_defs.values():
                if field_name not in defs:
                    all_have = False
                    break
                field_types.add(defs[field_name]["type"])
            if all_have and len(field_types) == 1:
                common_fields.add(field_name)

        # 4) 过滤条件中只保留公共字段，非公共字段忽略+打日志
        filtered_groups = []
        for group in retrieve_data.metadata_filters:
            filtered_conditions = []
            for cond in group.conditions:
                if cond.field in common_fields:
                    filtered_conditions.append(cond)
                else:
                    api_logger.warning(
                        f"[MetadataFilter] 字段 '{cond.field}' 不是公共字段（不是所有知识库都有或类型不一致），已忽略"
                    )
            if filtered_conditions:
                filtered_groups.append((group.logic, filtered_conditions))

        if not filtered_groups:
            api_logger.warning("[MetadataFilter] 过滤条件中无公共字段，跳过元数据过滤")
        else:
            all_document_ids = set()
            for pk_kb_id in private_kb_ids:
                metadata_defs = all_metadata_defs[pk_kb_id]

                engine = MetadataFilterEngine(db)
                filter_groups = [
                    FilterGroup(
                        conditions=[
                            FilterCondition(field=c.field, operator=c.operator, value=c.value)
                            for c in conditions
                        ],
                        logic=logic,
                    )
                    for logic, conditions in filtered_groups
                ]

                api_logger.info(
                    f"[MetadataFilter] executing filter for kb_id={pk_kb_id}, "
                    f"conditions={[{'field': c.field, 'op': c.operator, 'val': c.value} for g in filter_groups for c in g.conditions]}"
                )
                document_ids = engine.execute(
                    knowledge_id=pk_kb_id,
                    filter_groups=filter_groups,
                    metadata_defs=metadata_defs,
                )
                api_logger.info(
                    f"[MetadataFilter] kb_id={pk_kb_id}, "
                    f"filtered_count={len(document_ids)}, "
                    f"filtered_document_ids={sorted(str(d) for d in document_ids)}"
                )
                all_document_ids.update(document_ids)

            document_ids_filter = [str(d) for d in all_document_ids]
            api_logger.info(f"[MetadataFilter] final filter list: {document_ids_filter}")

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

    # default value is topk
    topn = retrieve_data.top_k

    # Helper: exclude documents in document_ids_filter (blacklist)
    exclude_ids = set(document_ids_filter) if document_ids_filter else set()
    def _exclude_filtered(docs):
        if not exclude_ids:
            return docs
        filtered = [d for d in docs if d.metadata.get("document_id") not in exclude_ids]
        api_logger.info(f"[MetadataFilter] post-filter: total={len(docs)}, excluded={len(docs) - len(filtered)}, remaining={len(filtered)}")
        return filtered

    # 1 participle search, 2 semantic search, 3 hybrid search
    match retrieve_data.retrieve_type:
        case chunk_schema.RetrieveType.PARTICIPLE:
            rs = vector_service.search_by_full_text(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.similarity_threshold, file_names_filter=retrieve_data.file_names_filter, document_ids_filter=document_ids_filter)
            rs = _exclude_filtered(rs)
            return success(data=jsonable_encoder(rs), msg="retrieval successful")
        case chunk_schema.RetrieveType.SEMANTIC:
            rs = vector_service.search_by_vector(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.vector_similarity_weight, file_names_filter=retrieve_data.file_names_filter, document_ids_filter=document_ids_filter)
            rs = _exclude_filtered(rs)
            return success(data=jsonable_encoder(rs), msg="retrieval successful")
        case _:
            rs1 = vector_service.search_by_vector(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.vector_similarity_weight, file_names_filter=retrieve_data.file_names_filter, document_ids_filter=document_ids_filter)
            rs2 = vector_service.search_by_full_text(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.similarity_threshold, file_names_filter=retrieve_data.file_names_filter, document_ids_filter=document_ids_filter)
            # Efficient deduplication
            seen_ids = set()
            unique_rs = []
            for doc in rs1 + rs2:
                if doc.metadata["doc_id"] not in seen_ids:
                    seen_ids.add(doc.metadata["doc_id"])
                    unique_rs.append(doc)
            rs = vector_service.rerank(query=retrieve_data.query, docs=unique_rs, top_k=retrieve_data.top_k) if unique_rs else []
            rerank_threshold = retrieve_data.rerank_score_threshold if retrieve_data.rerank_score_threshold is not None else (retrieve_data.vector_similarity_weight if retrieve_data.vector_similarity_weight is not None else 0.1)
            rs = [doc for doc in rs if doc.metadata.get("score", 0) > rerank_threshold]
            if retrieve_data.retrieve_type == chunk_schema.RetrieveType.Graph:
                kb_ids = [str(kb_id) for kb_id in private_kb_ids]
                workspace_ids = [str(workspace_id) for workspace_id in private_workspace_ids]
                llm_key = ModelApiKeyService.get_available_api_key(db, db_knowledge.llm_id)
                emb_key = ModelApiKeyService.get_available_api_key(db, db_knowledge.embedding_id)
                # Prepare to configure chat_mdl、embedding_model、vision_model information
                chat_model = Base(
                    key=llm_key.api_key,
                    model_name=llm_key.model_name,
                    base_url=llm_key.api_base
                )
                embedding_model = OpenAIEmbed(
                    key=emb_key.api_key,
                    model_name=emb_key.model_name,
                    base_url=emb_key.api_base
                )
                doc = kg_retriever.retrieval(question=retrieve_data.query, workspace_ids=workspace_ids, kb_ids=kb_ids, emb_mdl=embedding_model, llm=chat_model)
                if doc and doc['page_content'].strip() != '':
                    rs.insert(0, doc)
            rs = _exclude_filtered(rs)
            return success(data=jsonable_encoder(rs), msg="retrieval successful")
