import os
from typing import Any, Optional
from pathlib import Path
import shutil
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.models import file_model
from app.schemas import file_schema, document_schema
from app.schemas.response_schema import ApiResponse
from app.core.response_utils import success
from app.services import file_service, document_service
from app.core.logging_config import get_api_logger

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/files",
    tags=["files"]
)


@router.get("/{kb_id}/{parent_id}/files", response_model=ApiResponse)
async def get_files(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (file name)"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Paged query file list
    - Support filtering by kb_id and parent_id
    - Support keyword search for file names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    api_logger.info(f"Query file list: kb_id={kb_id}, parent_id={parent_id}, page={page}, pagesize={pagesize}, keywords={keywords}, username: {current_user.username}")
    # 1. parameter validation
    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Construct query conditions
    filters = [
        file_model.File.kb_id == kb_id
    ]
    if parent_id:
        filters.append(file_model.File.parent_id == parent_id)
    # Keyword search (fuzzy matching of file name)
    if keywords:
        filters.append(file_model.File.file_name.ilike(f"%{keywords}%"))

    # 3. Execute paged query
    try:
        api_logger.debug("Start executing file paging query")
        total, items = file_service.get_files_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            current_user=current_user
        )
        api_logger.info(f"File query successful: total={total}, returned={len(items)} records")
    except Exception as e:
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
    return success(data=result, msg="Query of file list succeeded")


@router.post("/folder", response_model=ApiResponse)
def create_folder(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        folder_name: str = '/',
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """
    Create a new folder
    """
    api_logger.info(f"Create folder request: kb_id={kb_id}, parent_id={parent_id}, folder_name={folder_name}, username: {current_user.username}")

    try:
        api_logger.debug(f"Start creating a folder: {folder_name}")
        create_folder = file_schema.FileCreate(
            kb_id=kb_id,
            created_by=current_user.id,
            parent_id=parent_id,
            file_name=folder_name,
            file_ext='folder',
            file_size=0,
        )
        db_file = file_service.create_file(db=db, file=create_folder, current_user=current_user)
        api_logger.info(f"Folder created successfully: {db_file.file_name} (ID: {db_file.id})")
        return success(data=file_schema.File.model_validate(db_file), msg="Folder creation successful")
    except Exception as e:
        api_logger.error(f"Folder creation failed: {folder_name} - {str(e)}")
        raise


@router.post("/file", response_model=ApiResponse)
async def upload_file(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    upload file
    """
    api_logger.info(f"upload file request: kb_id={kb_id}, parent_id={parent_id}, filename={file.filename}, username: {current_user.username}")

    # Read the contents of the file
    contents = await file.read()
    # Check file size
    file_size = len(contents)
    print(f"file size: {file_size} byte")
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file is empty."
        )
    # If the file size exceeds 50MB (50 * 1024 * 1024 bytes)
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The file size exceeds the {settings.MAX_FILE_SIZE}byte limit"
        )

    # Extract the extension using `os.path.splitext`
    _, file_extension = os.path.splitext(file.filename)
    upload_file = file_schema.FileCreate(
        kb_id=kb_id,
        created_by=current_user.id,
        parent_id=parent_id,
        file_name=file.filename,
        file_ext=file_extension.lower(),
        file_size=file_size,
    )
    db_file = file_service.create_file(db=db, file=upload_file, current_user=current_user)

    # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
    save_dir = os.path.join(settings.FILE_PATH, str(kb_id), str(parent_id))
    Path(save_dir).mkdir(parents=True, exist_ok=True)  # Ensure that the directory exists
    save_path = os.path.join(save_dir, f"{db_file.id}{file_extension}")

    # Save file
    with open(save_path, "wb") as f:
        f.write(contents)

    # Verify whether the file has been saved successfully
    if not os.path.exists(save_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File save failed"
        )

    # Create a document
    create_data = document_schema.DocumentCreate(
        kb_id=kb_id,
        created_by=current_user.id,
        file_id=db_file.id,
        file_name=db_file.file_name,
        file_ext=db_file.file_ext,
        file_size=db_file.file_size,
        file_meta={},
        parser_id="naive",
        parser_config={
            "layout_recognize": "DeepDOC",
            "chunk_token_num": 128,
            "delimiter": "\n",
            "auto_keywords": 0,
            "auto_questions": 0,
            "html4excel": "false"
        }
    )
    db_document = document_service.create_document(db=db, document=create_data, current_user=current_user)

    api_logger.info(f"File upload successfully: {file.filename} (file_id: {db_file.id}, document_id: {db_document.id})")
    return success(data=document_schema.Document.model_validate(db_document), msg="File upload successful")


@router.post("/customtext", response_model=ApiResponse)
async def custom_text(
        kb_id: uuid.UUID,
        parent_id: uuid.UUID,
        create_data: file_schema.CustomTextFileCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    custom text
    """
    api_logger.info(f"custom text upload request: kb_id={kb_id}, parent_id={parent_id}, title={create_data.title}, content={create_data.content}, username: {current_user.username}")

    # Check file content size
    # 将内容编码为字节（UTF-8）
    content_bytes = create_data.content.encode('utf-8')
    file_size = len(content_bytes)
    print(f"file size: {file_size} byte")
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The content is empty."
        )
    # If the file size exceeds 50MB (50 * 1024 * 1024 bytes)
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The content size exceeds the {settings.MAX_FILE_SIZE}byte limit"
        )

    upload_file = file_schema.FileCreate(
        kb_id=kb_id,
        created_by=current_user.id,
        parent_id=parent_id,
        file_name=f"{create_data.title}.txt",
        file_ext=".txt",
        file_size=file_size,
    )
    db_file = file_service.create_file(db=db, file=upload_file, current_user=current_user)

    # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
    save_dir = os.path.join(settings.FILE_PATH, str(kb_id), str(parent_id))
    Path(save_dir).mkdir(parents=True, exist_ok=True)  # Ensure that the directory exists
    save_path = os.path.join(save_dir, f"{db_file.id}.txt")

    # Save file
    with open(save_path, "wb") as f:
        f.write(content_bytes)

    # Verify whether the file has been saved successfully
    if not os.path.exists(save_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File save failed"
        )

    # Create a document
    create_document_data = document_schema.DocumentCreate(
        kb_id=kb_id,
        created_by=current_user.id,
        file_id=db_file.id,
        file_name=db_file.file_name,
        file_ext=db_file.file_ext,
        file_size=db_file.file_size,
        file_meta={},
        parser_id="naive",
        parser_config={
            "layout_recognize": "DeepDOC",
            "chunk_token_num": 128,
            "delimiter": "\n",
            "auto_keywords": 0,
            "auto_questions": 0,
            "html4excel": "false"
        }
    )
    db_document = document_service.create_document(db=db, document=create_document_data, current_user=current_user)

    api_logger.info(f"custom text upload successfully: {create_data.title} (file_id: {db_file.id}, document_id: {db_document.id})")
    return success(data=document_schema.Document.model_validate(db_document), msg="custom text upload successful")


@router.get("/{file_id}", response_model=Any)
async def get_file(
        file_id: uuid.UUID,
        db: Session = Depends(get_db)
) -> Any:
    """
    Download the file based on the file_id
    - Query file information from the database
    - Construct the file path and check if it exists
    - Return a FileResponse to download the file
    """
    api_logger.info(f"Download the file based on the file_id: file_id={file_id}")

    # 1. Query file information from the database
    db_file = file_service.get_file_by_id(db, file_id=file_id)
    if not db_file:
        api_logger.warning(f"The file does not exist or you do not have permission to access it: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist or you do not have permission to access it"
        )

    # 2. Construct file path：/files/{kb_id}/{parent_id}/{file.id}{file.file_ext}
    file_path = os.path.join(
        settings.FILE_PATH,
        str(db_file.kb_id),
        str(db_file.parent_id),
        f"{db_file.id}{db_file.file_ext}"
    )

    # 3. Check if the file exists
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found (possibly deleted)"
        )

    # 4.Return FileResponse (automatically handle download)
    return FileResponse(
        path=file_path,
        filename=db_file.file_name,  # Use original file name
        media_type="application/octet-stream"  # Universal binary stream type
    )


@router.put("/{file_id}", response_model=ApiResponse)
async def update_file(
        file_id: uuid.UUID,
        update_data: file_schema.FileUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Update file information (such as file name)
    - Only specified fields such as file_name are allowed to be modified
    """
    api_logger.debug(f"Query the file to be updated: {file_id}")

    # 1. Check if the file exists
    db_file = file_service.get_file_by_id(db, file_id=file_id)

    if not db_file:
        api_logger.warning(f"The file does not exist or you do not have permission to access it: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist or you do not have permission to access it"
        )

    # 2. Update fields (only update non-null fields)
    api_logger.debug(f"Start updating the file fields: {file_id}")
    updated_fields = []
    for field, value in update_data.items():
        if hasattr(db_file, field):
            old_value = getattr(db_file, field)
            if old_value != value:
                # update value
                setattr(db_file, field, value)
                updated_fields.append(f"{field}: {old_value} -> {value}")

    if updated_fields:
        api_logger.debug(f"updated fields: {', '.join(updated_fields)}")

    # 3. Save to database
    try:
        db.commit()
        db.refresh(db_file)
        api_logger.info(f"The file has been successfully updated: {db_file.file_name} (ID: {db_file.id})")
    except Exception as e:
        db.rollback()
        api_logger.error(f"File update failed: file_id={file_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File update failed: {str(e)}"
        )

    # 4. Return the updated file
    return success(data=file_schema.File.model_validate(db_file), msg="File information updated successfully")


@router.delete("/{file_id}", response_model=ApiResponse)
async def delete_file(
        file_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Delete a file or folder
    """
    api_logger.info(f"Request to delete file: file_id={file_id}, username: {current_user.username}")
    await _delete_file(db=db, file_id=file_id, current_user=current_user)
    return success(msg="File deleted successfully")

async def _delete_file(
        file_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
) -> None:
    """
    Delete a file or folder
    """
    # 1. Check if the file exists
    db_file = file_service.get_file_by_id(db, file_id=file_id)

    if not db_file:
        api_logger.warning(f"The file does not exist or you do not have permission to access it: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist or you do not have permission to access it"
        )

    # 2. Construct physical path
    file_path = Path(
        settings.FILE_PATH,
        str(db_file.kb_id),
        str(db_file.id)
    ) if db_file.file_ext == 'folder' else Path(
        settings.FILE_PATH,
        str(db_file.kb_id),
        str(db_file.parent_id),
        f"{db_file.id}{db_file.file_ext}"
    )

    # 3. Delete physical files/folders
    try:
        if file_path.exists():
            if db_file.file_ext == 'folder':
                shutil.rmtree(file_path)  # Recursively delete folders
            else:
                file_path.unlink()  # Delete a single file
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete physical file/folder: {str(e)}"
        )

    # 4.Delete db_file
    if db_file.file_ext == 'folder':
        db.query(file_model.File).filter(file_model.File.parent_id == db_file.id).delete()
    db.delete(db_file)
    db.commit()
