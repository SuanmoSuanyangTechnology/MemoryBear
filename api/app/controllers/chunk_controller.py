import os
from typing import Any, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.config import settings
from app.db import get_db
from app.core.rag.llm.cv_model import QWenCV
from app.dependencies import get_current_user
from app.models.user_model import User
from app.models.document_model import Document
from app.models import knowledge_model, knowledgeshare_model
from app.core.rag.models.chunk import DocumentChunk
from app.schemas import chunk_schema
from app.schemas.response_schema import ApiResponse
from app.core.response_utils import success
from app.services import knowledge_service, document_service, file_service, knowledgeshare_service
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.logging_config import get_api_logger

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

    # 5. Construct file path：/files/{kb_id}/{parent_id}/{file.id}{file.file_ext}
    file_path = os.path.join(
        settings.FILE_PATH,
        str(db_file.kb_id),
        str(db_file.parent_id),
        f"{db_file.id}{db_file.file_ext}"
    )

    # 6. Check if the file exists
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found (possibly deleted)"
        )

    # 7. Document parsing & segmentation
    def progress_callback(prog=None, msg=None):
        print(f"prog: {prog} msg: {msg}\n")
    # Prepare to configure vision_model information
    vision_model = QWenCV(
            key=db_knowledge.image2text.api_keys[0].api_key,
            model_name=db_knowledge.image2text.api_keys[0].model_name,
            lang="Chinese",  # Default to Chinese
            base_url=db_knowledge.image2text.api_keys[0].api_base
        )
    from app.core.rag.app.naive import chunk
    res = chunk(filename=file_path,
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
    return success(data=result, msg="Querying the document block preview list succeeded")


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
    return success(data=result, msg="Query of document chunk list succeeded")


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
    chunk = DocumentChunk(page_content=content, metadata=metadata)
    # 3. Segmented vector storage
    vector_service.add_chunks([chunk])

    # 4.update chunk_num
    db_document.chunk_num += 1
    db.commit()

    return success(data=chunk, msg="Document chunk creation successful")


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
        return success(data=items[0], msg="Document chunk query successful")
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
        vector_service.update_by_segment(chunk)
        return success(data=chunk, msg="The document chunk has been successfully updated")
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
        vector_service.delete_by_ids([doc_id])
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

    filters = [
        knowledge_model.Knowledge.id.in_(retrieve_data.kb_ids),
        knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Private,
        knowledge_model.Knowledge.chunk_num > 0,
        knowledge_model.Knowledge.status == 1
    ]
    existing_ids = knowledge_service.get_chunded_knowledgeids(
        db=db,
        filters=filters,
        current_user=current_user
    )
    filters = [
        knowledge_model.Knowledge.id.in_(retrieve_data.kb_ids),
        knowledge_model.Knowledge.permission_id == knowledge_model.PermissionType.Share,
        knowledge_model.Knowledge.chunk_num > 0,
        knowledge_model.Knowledge.status == 1
    ]
    share_ids = knowledge_service.get_chunded_knowledgeids(
        db=db,
        filters=filters,
        current_user=current_user
    )
    if share_ids:
        filters = [
            knowledgeshare_model.KnowledgeShare.target_kb_id.in_(retrieve_data.kb_ids)
        ]
        items = knowledgeshare_service.get_source_kb_ids_by_target_kb_id(
            db=db,
            filters=filters,
            current_user=current_user
        )
        existing_ids.extend(items)
    if not existing_ids:
        return success(data=[], msg="retrieval successful")
    kb_id = existing_ids[0]
    uuid_strs = [f"Vector_index_{kb_id}_Node".lower() for kb_id in existing_ids]
    indices = ",".join(uuid_strs)
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The knowledge base does not exist or access is denied"
        )

    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

    # 1 participle search, 2 semantic search, 3 hybrid search
    match retrieve_data.retrieve_type:
        case chunk_schema.RetrieveType.PARTICIPLE:
            rs = vector_service.search_by_full_text(query=retrieve_data.query, top_k=retrieve_data.top_k, indices=indices, score_threshold=retrieve_data.similarity_threshold)
            return success(data=rs, msg="retrieval successful")
        case chunk_schema.RetrieveType.SEMANTIC:
            rs = vector_service.search_by_vector(query=retrieve_data.query, top_k=retrieve_data.top_k, indices=indices, score_threshold=retrieve_data.vector_similarity_weight)
            return success(data=rs, msg="retrieval successful")
        case _:
            rs1 = vector_service.search_by_vector(query=retrieve_data.query, top_k=retrieve_data.top_k, indices=indices, score_threshold=retrieve_data.vector_similarity_weight)
            rs2 = vector_service.search_by_full_text(query=retrieve_data.query, top_k=retrieve_data.top_k, indices=indices, score_threshold=retrieve_data.similarity_threshold)
            # Efficient deduplication
            seen_ids = set()
            unique_rs = []
            for doc in rs1 + rs2:
                if doc.metadata["doc_id"] not in seen_ids:
                    seen_ids.add(doc.metadata["doc_id"])
                    unique_rs.append(doc)
            rs = vector_service.rerank(query=retrieve_data.query, docs=unique_rs, top_k=retrieve_data.top_k)
            return success(data=rs, msg="retrieval successful")