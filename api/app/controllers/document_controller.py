import os
from typing import Optional
import datetime
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.models import document_model
from app.schemas import document_schema
from app.schemas.response_schema import ApiResponse
from app.core.response_utils import success
from app.services import document_service, file_service, knowledge_service
from app.controllers import file_controller
from app.celery_app import celery_app
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.logging_config import get_api_logger

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_user)]  # Apply auth to all routes in this controller
)


@router.get("/{kb_id}/{parent_id}/documents", response_model=ApiResponse)
async def get_documents(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (file name)"),
        document_ids: Optional[str] = Query(None, description="document ids, separated by commas"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
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
        files = file_service.get_files_by_parent_id(db=db, parent_id=parent_id, current_user=current_user)
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
        total, items = document_service.get_documents_paginated(
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
    return success(data=result, msg="Query of document list succeeded")


@router.post("/document", response_model=ApiResponse)
async def create_document(
        create_data: document_schema.DocumentCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    create document
    """
    api_logger.info(f"Create document request: file_name={create_data.file_name}, kb_id={create_data.kb_id}, username: {current_user.username}")

    try:
        api_logger.debug(f"Start creating a document: {create_data.file_name}")
        db_document = document_service.create_document(db=db, document=create_data, current_user=current_user)
        api_logger.info(f"Document created successfully: {db_document.file_name} (ID: {db_document.id})")
        return success(data=document_schema.Document.model_validate(db_document), msg="Document creation successful")
    except Exception as e:
        api_logger.error(f"Document creation failed: {create_data.file_name} - {str(e)}")
        raise


@router.get("/{document_id}", response_model=ApiResponse)
async def get_document(
        document_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve document information based on document_id
    """
    api_logger.info(f"Obtain document information: document_id={document_id}, username: {current_user.username}")

    try:
        # 1. Query document information from the database
        api_logger.debug(f"query documentation: {document_id}")
        db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)
        if not db_document:
            api_logger.warning(f"The document does not exist or you do not have access: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The document does not exist or you do not have access"
            )

        api_logger.info(f"Document query successful: {db_document.file_name} (ID: {db_document.id})")
        return success(data=document_schema.Document.model_validate(db_document), msg="Successfully obtained document information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Document query failed: document_id={document_id} - {str(e)}")
        raise


@router.put("/{document_id}", response_model=ApiResponse)
async def update_document(
        document_id: uuid.UUID,
        update_data: document_schema.DocumentUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Update document information
    """
    # 1. Check if the document exists
    api_logger.debug(f"Query the document to be updated: {document_id}")
    db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)

    if not db_document:
        api_logger.warning(f"The document does not exist or you do not have permission to access it: document_id={document_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document does not exist or you do not have permission to access it"
        )

    # 2. If updating the status, synchronize the document status switch to whether it can be retrieved from the vector database
    update_dict = update_data.dict(exclude_unset=True)
    if "status" in update_dict:
        new_status = update_dict["status"]
        if new_status != db_document.status:
            db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=db_document.kb_id, current_user=current_user)
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
            vector_service.change_status_by_document_id(document_id=str(document_id), status=new_status)

    # 3. Update fields (only update non-null fields)
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

    db_document.updated_at = datetime.datetime.now()

    # 4. Save to database
    try:
        db.commit()
        db.refresh(db_document)
        api_logger.info(f"The document has been successfully updated: {db_document.file_name} (ID: {db_document.id})")
    except Exception as e:
        db.rollback()
        api_logger.error(f"Document update failed: document_id={document_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document update failed: {str(e)}"
        )

    # 5. Return the updated document
    return success(data=document_schema.Document.model_validate(db_document), msg="Document information updated successfully")


@router.delete("/{document_id}", response_model=ApiResponse)
async def delete_document(
        document_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Delete document
    """
    api_logger.info(f"Request to delete document: document_id={document_id}, username: {current_user.username}")

    try:
        # 1. Check if the document exists
        api_logger.debug(f"Check whether the document exists: {document_id}")
        db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)

        if not db_document:
            api_logger.warning(f"The document does not exist or you do not have permission to access it: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The document does not exist or you do not have permission to access it"
            )
        file_id = db_document.file_id

        # 2. Delete document
        api_logger.debug(f"Perform document delete: {db_document.file_name} (ID: {document_id})")
        db.delete(db_document)
        db.commit()

        # 3. Delete file
        await file_controller._delete_file(db=db, file_id=file_id, current_user=current_user)

        # 4. Delete vector index
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=db_document.kb_id, current_user=current_user)
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        vector_service.delete_by_metadata_field(key="document_id", value=str(document_id))

        api_logger.info(f"The document has been successfully deleted: {db_document.file_name} (ID: {document_id})")
        return success(msg="The document has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the document: document_id={document_id} - {str(e)}")
        raise


@router.post("/{document_id}/chunks", response_model=ApiResponse)
async def parse_documents(
        document_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    parse document
    """
    api_logger.info(f"Request to parse document: document_id={document_id}, username: {current_user.username}")

    try:
        # 1. Check if the document exists
        api_logger.debug(f"Check whether the document exists: {document_id}")
        db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)

        if not db_document:
            api_logger.warning(f"The document does not exist or you do not have permission to access it: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The document does not exist or you do not have permission to access it"
            )

        # 2. Check if the file exists
        api_logger.debug(f"Check whether the file exists: {db_document.file_id}")
        db_file = file_service.get_file_by_id(db, file_id=db_document.file_id)

        if not db_file:
            api_logger.warning(f"The file does not exist or you do not have permission to access it: file_id={db_document.file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The file does not exist or you do not have permission to access it"
            )

        # 3. Construct file path：/files/{kb_id}/{parent_id}/{file.id}{file.file_ext}
        file_path = os.path.join(
            settings.FILE_PATH,
            str(db_file.kb_id),
            str(db_file.parent_id),
            f"{db_file.id}{db_file.file_ext}"
        )

        # 4. Check if the file exists
        if not os.path.exists(file_path):
            api_logger.warning(f"File not found (possibly deleted): file_path={file_path}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found (possibly deleted)"
            )

        # 5. Obtain knowledge base information
        api_logger.info( f"Obtain details of the knowledge base: knowledge_id={db_document.kb_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=db_document.kb_id, current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or access is denied: knowledge_id={db_document.kb_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or access is denied"
            )

        # 6. Task: Document parsing, vectorization, and storage
        # from app.tasks import parse_document
        # parse_document(file_path, document_id)
        task = celery_app.send_task("app.core.rag.tasks.parse_document", args=[file_path, document_id])
        result = {
            "task_id": task.id
        }
        return success(data=result, msg="Task accepted. The document is being processed in the background.")
    except Exception as e:
        api_logger.error(f"Failed to parse document: document_id={document_id} - {str(e)}")
        raise
