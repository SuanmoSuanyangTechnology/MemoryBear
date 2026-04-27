import os
from typing import Any, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models import file_model
from app.models.user_model import User
from app.schemas import file_schema, document_schema
from app.schemas.response_schema import ApiResponse
from app.services import file_service, document_service
from app.services.knowledge_service import get_knowledge_by_id as get_kb_by_id
from app.services.file_storage_service import (
    FileStorageService,
    generate_kb_file_key,
    get_file_storage_service,
)

api_logger = get_api_logger()

router = APIRouter(
    prefix="/files",
    tags=["files"]
)


@router.get("/{kb_id}/{parent_id}/files", response_model=ApiResponse)
async def get_files(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        page: int = Query(1, gt=0),
        pagesize: int = Query(20, gt=0, le=100),
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (file name)"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """Paged query file list"""
    api_logger.info(f"Query file list: kb_id={kb_id}, parent_id={parent_id}, page={page}, pagesize={pagesize}")

    if page < 1 or pagesize < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The paging parameter must be greater than 0")

    filters = [file_model.File.kb_id == kb_id]
    if parent_id:
        filters.append(file_model.File.parent_id == parent_id)
    if keywords:
        filters.append(file_model.File.file_name.ilike(f"%{keywords}%"))

    try:
        total, items = file_service.get_files_paginated(
            db=db, filters=filters, page=page, pagesize=pagesize,
            orderby=orderby, desc=desc, current_user=current_user
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Query failed: {str(e)}")

    result = {
        "items": items,
        "page": {"page": page, "pagesize": pagesize, "total": total, "has_next": page * pagesize < total}
    }
    return success(data=jsonable_encoder(result), msg="Query of file list succeeded")


@router.post("/folder", response_model=ApiResponse)
async def create_folder(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        folder_name: str = '/',
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """Create a new folder"""
    api_logger.info(f"Create folder request: kb_id={kb_id}, parent_id={parent_id}, folder_name={folder_name}")
    try:
        create_folder_data = file_schema.FileCreate(
            kb_id=kb_id, created_by=current_user.id, parent_id=parent_id,
            file_name=folder_name, file_ext='folder', file_size=0,
        )
        db_file = file_service.create_file(db=db, file=create_folder_data, current_user=current_user)
        return success(data=jsonable_encoder(file_schema.File.model_validate(db_file)), msg="Folder creation successful")
    except Exception as e:
        api_logger.error(f"Folder creation failed: {folder_name} - {str(e)}")
        raise


@router.post("/file", response_model=ApiResponse)
async def upload_file(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """Upload file to storage backend"""
    api_logger.info(f"upload file request: kb_id={kb_id}, parent_id={parent_id}, filename={file.filename}")

    contents = await file.read()
    file_size = len(contents)
    if file_size == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The file is empty.")
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"File size exceeds {settings.MAX_FILE_SIZE} byte limit")

    _, file_extension = os.path.splitext(file.filename)
    file_ext = file_extension.lower()

    # Create File record
    upload_file_data = file_schema.FileCreate(
        kb_id=kb_id, created_by=current_user.id, parent_id=parent_id,
        file_name=file.filename, file_ext=file_ext, file_size=file_size,
    )
    db_file = file_service.create_file(db=db, file=upload_file_data, current_user=current_user)

    # Upload to storage backend
    file_key = generate_kb_file_key(kb_id=kb_id, file_id=db_file.id, file_ext=file_ext)
    try:
        await storage_service.storage.upload(file_key=file_key, content=contents, content_type=file.content_type)
    except Exception as e:
        api_logger.error(f"Storage upload failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"File storage failed: {str(e)}")

    # Save file_key
    db_file.file_key = file_key
    db.commit()
    db.refresh(db_file)

    # Create document (inherit parser_config from knowledge base)
    default_parser_config = {
        "layout_recognize": "DeepDOC", "chunk_token_num": 128, "delimiter": "\n",
        "auto_keywords": 0, "auto_questions": 0, "html4excel": "false"
    }
    try:
        db_knowledge = get_kb_by_id(db, knowledge_id=kb_id, current_user=current_user)
        if db_knowledge and db_knowledge.parser_config:
            default_parser_config.update(dict(db_knowledge.parser_config))
    except Exception:
        pass

    create_data = document_schema.DocumentCreate(
        kb_id=kb_id, created_by=current_user.id, file_id=db_file.id,
        file_name=db_file.file_name, file_ext=db_file.file_ext, file_size=db_file.file_size,
        file_meta={}, parser_id="naive", parser_config=default_parser_config
    )
    db_document = document_service.create_document(db=db, document=create_data, current_user=current_user)

    api_logger.info(f"File upload successfully: {file.filename} (file_id: {db_file.id}, document_id: {db_document.id})")
    return success(data=jsonable_encoder(document_schema.Document.model_validate(db_document)), msg="File upload successful")


@router.post("/customtext", response_model=ApiResponse)
async def custom_text(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        create_data: file_schema.CustomTextFileCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """Custom text upload"""
    content_bytes = create_data.content.encode('utf-8')
    file_size = len(content_bytes)
    if file_size == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The content is empty.")
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Content size exceeds {settings.MAX_FILE_SIZE} byte limit")

    upload_file_data = file_schema.FileCreate(
        kb_id=kb_id, created_by=current_user.id, parent_id=parent_id,
        file_name=f"{create_data.title}.txt", file_ext=".txt", file_size=file_size,
    )
    db_file = file_service.create_file(db=db, file=upload_file_data, current_user=current_user)

    # Upload to storage backend
    file_key = generate_kb_file_key(kb_id=kb_id, file_id=db_file.id, file_ext=".txt")
    try:
        await storage_service.storage.upload(file_key=file_key, content=content_bytes, content_type="text/plain")
    except Exception as e:
        api_logger.error(f"Storage upload failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"File storage failed: {str(e)}")

    db_file.file_key = file_key
    db.commit()
    db.refresh(db_file)

    create_document_data = document_schema.DocumentCreate(
        kb_id=kb_id, created_by=current_user.id, file_id=db_file.id,
        file_name=db_file.file_name, file_ext=db_file.file_ext, file_size=db_file.file_size,
        file_meta={}, parser_id="naive",
        parser_config={"layout_recognize": "DeepDOC", "chunk_token_num": 128, "delimiter": "\n",
                       "auto_keywords": 0, "auto_questions": 0, "html4excel": "false"}
    )
    db_document = document_service.create_document(db=db, document=create_document_data, current_user=current_user)

    return success(data=jsonable_encoder(document_schema.Document.model_validate(db_document)), msg="custom text upload successful")


@router.get("/{file_id}", response_model=Any)
async def get_file(
        file_id: uuid.UUID,
        db: Session = Depends(get_db),
        storage_service: FileStorageService = Depends(get_file_storage_service),
) -> Any:
    """Download file by file_id"""
    db_file = file_service.get_file_by_id(db, file_id=file_id)
    if not db_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if not db_file.file_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File has no storage key (legacy data not migrated)")

    try:
        content = await storage_service.download_file(db_file.file_key)
    except Exception as e:
        api_logger.error(f"Storage download failed: {e}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found in storage")

    import mimetypes
    media_type = mimetypes.guess_type(db_file.file_name)[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{db_file.file_name}"'}
    )


@router.put("/{file_id}", response_model=ApiResponse)
async def update_file(
        file_id: uuid.UUID,
        update_data: file_schema.FileUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """Update file information (such as file name)"""
    db_file = file_service.get_file_by_id(db, file_id=file_id)
    if not db_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    for field, value in update_data.dict(exclude_unset=True).items():
        if hasattr(db_file, field):
            setattr(db_file, field, value)

    try:
        db.commit()
        db.refresh(db_file)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"File update failed: {str(e)}")

    return success(data=jsonable_encoder(file_schema.File.model_validate(db_file)), msg="File information updated successfully")


@router.delete("/{file_id}", response_model=ApiResponse)
async def delete_file(
        file_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
        storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """Delete a file or folder"""
    api_logger.info(f"Request to delete file: file_id={file_id}")
    await _delete_file(db=db, file_id=file_id, current_user=current_user, storage_service=storage_service)
    return success(msg="File deleted successfully")


async def _delete_file(
        file_id: uuid.UUID,
        db: Session,
        current_user: User,
        storage_service: FileStorageService,
) -> None:
    """Delete a file or folder from storage and database"""
    db_file = file_service.get_file_by_id(db, file_id=file_id)
    if not db_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Delete from storage backend
    if db_file.file_ext == 'folder':
        # For folders, delete all child files from storage first
        child_files = db.query(file_model.File).filter(file_model.File.parent_id == db_file.id).all()
        for child in child_files:
            if child.file_key:
                try:
                    await storage_service.delete_file(child.file_key)
                except Exception as e:
                    api_logger.warning(f"Failed to delete child file from storage: {child.file_key} - {e}")
        db.query(file_model.File).filter(file_model.File.parent_id == db_file.id).delete()
    else:
        if db_file.file_key:
            try:
                await storage_service.delete_file(db_file.file_key)
            except Exception as e:
                api_logger.warning(f"Failed to delete file from storage: {db_file.file_key} - {e}")

    db.delete(db_file)
    db.commit()
