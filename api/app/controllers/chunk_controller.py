import os
import csv
import io
from typing import Any, Optional
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_api_logger
from app.core.rag.common.settings import kg_retriever
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.cv_model import QWenCV
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
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
    parent_child_mode = db_document.parser_config.get("parent_child_mode", False)

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
                "file_created_at": int(db_document.created_at.timestamp() * 1000),
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
                "file_created_at": int(db_document.created_at.timestamp() * 1000),
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
                "file_created_at": int(db_document.created_at.timestamp() * 1000),
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
    分页查询文档分块预览（嵌套结构）
    - 支持普通分块、父子分块、QA 分块三种模式
    - 返回嵌套的 DocumentChunk 结构，children 字段包含子块
    - 分页按父块（顶层 chunk）层级切片
    """
    api_logger.info(f"Paged query document chunk preview hierarchy: kb_id={kb_id}, document_id={document_id}, page={page}, pagesize={pagesize}")

    # 1. 参数校验
    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. 获取知识库信息
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    # 3. 检查文档
    db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    # 4. 检查文件
    db_file = file_service.get_file_by_id(db, file_id=db_document.file_id)
    if not db_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist or you do not have permission to access it"
        )

    # 5. 获取文件内容
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

    # 6. 文档解析 & 分块
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

    # 如果传入了覆盖参数，直接合并
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

    # 7. 父块分页
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
    - Return paging metadata + file list
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

    # 3. Execute paged query
    try:
        api_logger.debug("Start executing document chunk query")
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        total, items = vector_service.search_by_segment(document_id=str(document_id), query=keywords, pagesize=pagesize, page=page, asc=True)
        api_logger.info(f"Document chunk query successful: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"Document chunk query failed: {str(e)}")
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
        "file_created_at": int(db_document.created_at.timestamp() * 1000),
        "document_id": str(document_id),
        "knowledge_id": str(kb_id),
        "sort_id": sort_id,
        "status": 1,
    }
    # QA chunk: 注入 chunk_type/question/answer 到 metadata
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

    db_document = db.query(Document).filter(Document.id == document_id).first()
    if not db_document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The document does not exist or you do not have permission to access it")

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
            "file_created_at": int(db_document.created_at.timestamp() * 1000),
            "document_id": str(document_id),
            "knowledge_id": str(kb_id),
            "sort_id": sort_id,
            "status": 1,
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
        parent_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
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
            knowledgeshare_model.KnowledgeShare.target_kb_id.in_(kb_ids)
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

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

    # default value is topk
    topn = retrieve_data.top_k

    # 1 participle search, 2 semantic search, 3 hybrid search
    match retrieve_data.retrieve_type:
        case chunk_schema.RetrieveType.PARTICIPLE:
            rs = vector_service.search_by_full_text(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.similarity_threshold, file_names_filter=retrieve_data.file_names_filter)
            return success(data=jsonable_encoder(rs), msg="retrieval successful")
        case chunk_schema.RetrieveType.SEMANTIC:
            rs = vector_service.search_by_vector(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.vector_similarity_weight, file_names_filter=retrieve_data.file_names_filter)
            return success(data=jsonable_encoder(rs), msg="retrieval successful")
        case _:
            rs1 = vector_service.search_by_vector(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.vector_similarity_weight, file_names_filter=retrieve_data.file_names_filter)
            rs2 = vector_service.search_by_full_text(query=retrieve_data.query, top_k=topn, indices=indices, score_threshold=retrieve_data.similarity_threshold, file_names_filter=retrieve_data.file_names_filter)
            # Efficient deduplication
            seen_ids = set()
            unique_rs = []
            for doc in rs1 + rs2:
                if doc.metadata["doc_id"] not in seen_ids:
                    seen_ids.add(doc.metadata["doc_id"])
                    unique_rs.append(doc)
            rs = vector_service.rerank(query=retrieve_data.query, docs=unique_rs, top_k=retrieve_data.top_k) if unique_rs else []
            # api_logger.debug(f"Rerank result before: {rs}")
            rerank_threshold = retrieve_data.rerank_score_threshold if retrieve_data.rerank_score_threshold is not None else (retrieve_data.vector_similarity_weight if retrieve_data.vector_similarity_weight is not None else 0.1)
            rs = [doc for doc in rs if doc.metadata.get("score", 0) > rerank_threshold]
            # api_logger.debug(f"Rerank result  after: {rs}")
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
                if doc:
                    rs.insert(0, doc)
            return success(data=jsonable_encoder(rs), msg="retrieval successful")