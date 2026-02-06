"""
File storage controller module.

This module provides API endpoints for file storage operations using the
configurable storage backend. It is a new controller that does not modify
the existing file_controller.py.

Routes:
    POST /storage/files - Upload a file
    GET /storage/files/{file_id} - Download a file
    DELETE /storage/files/{file_id} - Delete a file
"""

import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.core.storage import LocalStorage
from app.core.storage.url_signer import generate_signed_url, verify_signed_url
from app.core.storage_exceptions import (
    StorageDeleteError,
    StorageUploadError,
)
from app.db import get_db
from app.dependencies import get_current_user, get_share_user_id, ShareTokenData
from app.models.file_metadata_model import FileMetadata
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.services.file_storage_service import (
    FileStorageService,
    generate_file_key,
    get_file_storage_service,
)

api_logger = get_api_logger()

router = APIRouter(
    prefix="/storage",
    tags=["storage"]
)


@router.post("/files", response_model=ApiResponse)
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    Upload a file to the configured storage backend.
    """
    tenant_id = current_user.tenant_id
    workspace_id = current_user.current_workspace_id

    api_logger.info(
        f"Storage upload request: tenant_id={tenant_id}, workspace_id={workspace_id}, "
        f"filename={file.filename}, username={current_user.username}"
    )

    # Read file contents
    contents = await file.read()
    file_size = len(contents)

    # Validate file size
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file is empty."
        )

    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The file size exceeds the {settings.MAX_FILE_SIZE} byte limit"
        )

    # Extract file extension
    _, file_extension = os.path.splitext(file.filename)
    file_ext = file_extension.lower()

    # Generate file_id and file_key
    file_id = uuid.uuid4()
    file_key = generate_file_key(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file_id=file_id,
        file_ext=file_ext,
    )

    # Create file metadata record with pending status
    file_metadata = FileMetadata(
        id=file_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file_key=file_key,
        file_name=file.filename,
        file_ext=file_ext,
        file_size=file_size,
        content_type=file.content_type,
        status="pending",
    )
    db.add(file_metadata)
    db.commit()
    db.refresh(file_metadata)

    # Upload file to storage backend
    try:
        await storage_service.upload_file(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_id=file_id,
            file_ext=file_ext,
            content=contents,
            content_type=file.content_type,
        )
        # Update status to completed
        file_metadata.status = "completed"
        db.commit()
        api_logger.info(f"File uploaded to storage: file_key={file_key}")
    except StorageUploadError as e:
        # Update status to failed
        file_metadata.status = "failed"
        db.commit()
        api_logger.error(f"Storage upload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File storage failed: {str(e)}"
        )

    api_logger.info(f"File upload successful: {file.filename} (file_id: {file_id})")

    return success(
        data={"file_id": str(file_id), "file_key": file_key},
        msg="File upload successful"
    )


@router.post("/share/files", response_model=ApiResponse)
async def upload_file_with_share_token(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    share_data: ShareTokenData = Depends(get_share_user_id),
    storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    Upload a file to the configured storage backend using share_token authentication.
    """
    from app.services.release_share_service import ReleaseShareService
    from app.models.app_model import App
    from app.models.workspace_model import Workspace
    
    # Get share and release info from share_token
    service = ReleaseShareService(db)
    share_info = service.get_shared_release_info(share_token=share_data.share_token)
    
    # Get share object to access app_id
    share = service.repo.get_by_share_token(share_data.share_token)
    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shared app not found"
        )
    
    # Get app to access workspace_id
    app = db.query(App).filter(
        App.id == share.app_id,
        App.is_active.is_(True)
    ).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    # Get workspace to access tenant_id
    workspace = db.query(Workspace).filter(
        Workspace.id == app.workspace_id
    ).first()
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found"
        )
    
    tenant_id = workspace.tenant_id
    workspace_id = app.workspace_id

    api_logger.info(
        f"Storage upload request (share): tenant_id={tenant_id}, workspace_id={workspace_id}, "
        f"filename={file.filename}, share_token={share_data.share_token}"
    )

    # Read file contents
    contents = await file.read()
    file_size = len(contents)

    # Validate file size
    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file is empty."
        )

    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The file size exceeds the {settings.MAX_FILE_SIZE} byte limit"
        )

    # Extract file extension
    _, file_extension = os.path.splitext(file.filename)
    file_ext = file_extension.lower()

    # Generate file_id and file_key
    file_id = uuid.uuid4()
    file_key = generate_file_key(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file_id=file_id,
        file_ext=file_ext,
    )

    # Create file metadata record with pending status
    file_metadata = FileMetadata(
        id=file_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        file_key=file_key,
        file_name=file.filename,
        file_ext=file_ext,
        file_size=file_size,
        content_type=file.content_type,
        status="pending",
    )
    db.add(file_metadata)
    db.commit()
    db.refresh(file_metadata)

    # Upload file to storage backend
    try:
        await storage_service.upload_file(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_id=file_id,
            file_ext=file_ext,
            content=contents,
            content_type=file.content_type,
        )
        # Update status to completed
        file_metadata.status = "completed"
        db.commit()
        api_logger.info(f"File uploaded to storage (share): file_key={file_key}")
    except StorageUploadError as e:
        # Update status to failed
        file_metadata.status = "failed"
        db.commit()
        api_logger.error(f"Storage upload failed (share): {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File storage failed: {str(e)}"
        )

    api_logger.info(f"File upload successful (share): {file.filename} (file_id: {file_id})")

    return success(
        data={"file_id": str(file_id), "file_key": file_key},
        msg="File upload successful"
    )


@router.get("/files/{file_id}", response_model=Any)
async def download_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage_service: FileStorageService = Depends(get_file_storage_service),
) -> Any:
    """
    Download a file from the configured storage backend.
    """
    api_logger.info(f"Storage download request: file_id={file_id}")

    # Query file metadata from database
    file_metadata = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_metadata:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist"
        )

    if file_metadata.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File upload not completed, status: {file_metadata.status}"
        )

    file_key = file_metadata.file_key
    storage = storage_service.storage

    if isinstance(storage, LocalStorage):
        full_path = storage._get_full_path(file_key)

        if not full_path.exists():
            api_logger.warning(f"File not found on disk: file_key={file_key}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found (possibly deleted)"
            )

        api_logger.info(f"Serving local file: file_key={file_key}")
        return FileResponse(
            path=str(full_path),
            filename=file_metadata.file_name,
            media_type=file_metadata.content_type or "application/octet-stream"
        )
    else:
        try:
            presigned_url = await storage_service.get_file_url(file_key, expires=3600)
            api_logger.info(f"Redirecting to presigned URL: file_key={file_key}")
            return RedirectResponse(url=presigned_url, status_code=status.HTTP_302_FOUND)
        except FileNotFoundError:
            api_logger.warning(f"File not found in remote storage: file_key={file_key}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found in storage"
            )
        except Exception as e:
            api_logger.error(f"Failed to get presigned URL: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve file: {str(e)}"
            )


@router.delete("/files/{file_id}", response_model=ApiResponse)
async def delete_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    Delete a file from the configured storage backend.
    """
    api_logger.info(
        f"Storage delete request: file_id={file_id}, username={current_user.username}"
    )

    # Query file metadata from database
    file_metadata = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_metadata:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist"
        )

    file_key = file_metadata.file_key

    # Delete file from storage
    try:
        deleted = await storage_service.delete_file(file_key)
        if deleted:
            api_logger.info(f"File deleted from storage: file_key={file_key}")
        else:
            api_logger.info(f"File did not exist in storage: file_key={file_key}")
    except StorageDeleteError as e:
        api_logger.error(f"Storage delete failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file from storage: {str(e)}"
        )

    # Delete database record
    try:
        db.delete(file_metadata)
        db.commit()
        api_logger.info(f"File record deleted from database: file_id={file_id}")
    except Exception as e:
        api_logger.error(f"Database delete failed: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file record: {str(e)}"
        )

    return success(msg="File deleted successfully")


@router.get("/files/{file_id}/url", response_model=ApiResponse)
async def get_file_url(
    file_id: uuid.UUID,
    expires: int = None,
    permanent: bool = False,
    db: Session = Depends(get_db),
    storage_service: FileStorageService = Depends(get_file_storage_service),
):
    """
    Get an access URL for a file (no authentication required).

    Args:
        file_id: The UUID of the file.
        expires: URL validity period in seconds (default from FILE_URL_EXPIRES env).
        permanent: If True, return a permanent URL without expiration.
        db: Database session.
        storage_service: The file storage service.

    Returns:
        ApiResponse with the access URL.
    """
    if expires is None:
        expires = settings.FILE_URL_EXPIRES

    api_logger.info(f"Get file URL request: file_id={file_id}, expires={expires}, permanent={permanent}")

    # Query file metadata from database
    file_metadata = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_metadata:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist"
        )

    if file_metadata.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File upload not completed, status: {file_metadata.status}"
        )

    file_key = file_metadata.file_key
    storage = storage_service.storage

    try:
        if permanent:
            # Generate permanent URL (no expiration check)
            server_url = settings.FILE_LOCAL_SERVER_URL
            url = f"{server_url}/storage/permanent/{file_id}"
            return success(
                data={
                    "url": url,
                    "expires_in": None,
                    "permanent": True,
                    "file_name": file_metadata.file_name,
                },
                msg="Permanent file URL generated successfully"
            )

        if isinstance(storage, LocalStorage):
            # For local storage, generate signed URL with expiration
            url = generate_signed_url(str(file_id), expires)
        else:
            # For remote storage (OSS/S3), get presigned URL
            url = await storage_service.get_file_url(file_key, expires=expires)

        api_logger.info(f"Generated file URL: file_id={file_id}")
        return success(
            data={
                "url": url,
                "expires_in": expires,
                "permanent": False,
                "file_name": file_metadata.file_name,
            },
            msg="File URL generated successfully"
        )
    except Exception as e:
        api_logger.error(f"Failed to generate file URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate file URL: {str(e)}"
        )


@router.get("/public/{file_id}", response_model=Any)
async def public_download_file(
    file_id: uuid.UUID,
    expires: int = 0,
    signature: str = "",
    db: Session = Depends(get_db),
    storage_service: FileStorageService = Depends(get_file_storage_service),
) -> Any:
    """
    Public file download endpoint with signature verification.

    This endpoint allows downloading files without authentication,
    but requires a valid signature and non-expired timestamp.

    Args:
        file_id: The UUID of the file.
        expires: Expiration timestamp.
        signature: HMAC signature for verification.
        db: Database session.
        storage_service: The file storage service.

    Returns:
        FileResponse for the requested file.
    """
    api_logger.info(f"Public download request: file_id={file_id}")

    # Verify signature
    is_valid, error_msg = verify_signed_url(str(file_id), expires, signature)
    if not is_valid:
        api_logger.warning(f"Invalid signed URL: file_id={file_id}, error={error_msg}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_msg
        )

    # Query file metadata from database
    file_metadata = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_metadata:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist"
        )

    if file_metadata.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File upload not completed, status: {file_metadata.status}"
        )

    file_key = file_metadata.file_key
    storage = storage_service.storage

    if isinstance(storage, LocalStorage):
        full_path = storage._get_full_path(file_key)

        if not full_path.exists():
            api_logger.warning(f"File not found on disk: file_key={file_key}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        api_logger.info(f"Serving public file: file_key={file_key}")
        return FileResponse(
            path=str(full_path),
            filename=file_metadata.file_name,
            media_type=file_metadata.content_type or "application/octet-stream"
        )
    else:
        # For remote storage, redirect to presigned URL
        try:
            presigned_url = await storage_service.get_file_url(file_key, expires=3600)
            return RedirectResponse(url=presigned_url, status_code=status.HTTP_302_FOUND)
        except Exception as e:
            api_logger.error(f"Failed to get presigned URL: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve file: {str(e)}"
            )


@router.get("/permanent/{file_id}", response_model=Any)
async def permanent_download_file(
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage_service: FileStorageService = Depends(get_file_storage_service),
) -> Any:
    """
    Permanent file download endpoint (no expiration, no signature required).

    This endpoint allows downloading files without authentication or expiration.
    Use with caution as URLs are permanently accessible.

    Args:
        file_id: The UUID of the file.
        db: Database session.
        storage_service: The file storage service.

    Returns:
        FileResponse for the requested file.
    """
    api_logger.info(f"Permanent download request: file_id={file_id}")

    # Query file metadata from database
    file_metadata = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_metadata:
        api_logger.warning(f"File not found in database: file_id={file_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The file does not exist"
        )

    if file_metadata.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File upload not completed, status: {file_metadata.status}"
        )

    file_key = file_metadata.file_key
    storage = storage_service.storage

    if isinstance(storage, LocalStorage):
        full_path = storage._get_full_path(file_key)

        if not full_path.exists():
            api_logger.warning(f"File not found on disk: file_key={file_key}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found"
            )

        api_logger.info(f"Serving permanent file: file_key={file_key}")
        return FileResponse(
            path=str(full_path),
            filename=file_metadata.file_name,
            media_type=file_metadata.content_type or "application/octet-stream"
        )
    else:
        # For remote storage, redirect to presigned URL with long expiration
        try:
            # Use a very long expiration (7 days max for most cloud providers)
            presigned_url = await storage_service.get_file_url(file_key, expires=604800)
            return RedirectResponse(url=presigned_url, status_code=status.HTTP_302_FOUND)
        except Exception as e:
            api_logger.error(f"Failed to get presigned URL: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to retrieve file: {str(e)}"
            )
