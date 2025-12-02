"""
Upload Controller for Generic File Upload System
Handles HTTP requests for file upload, download, deletion, and metadata updates.
"""
import os
import json
from typing import List, Optional, Any
from pathlib import Path
from fastapi import APIRouter, Depends, File, UploadFile, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.schemas.generic_file_schema import (
    GenericFileResponse,
    FileMetadataUpdate,
    UploadResultSchema,
    BatchUploadResponse
)
from app.core.response_utils import success, fail
from app.core.upload_enums import UploadContext
from app.services.upload_service import UploadService
from app.core.logging_config import get_logger
from app.core.exceptions import (
    ValidationException,
    ResourceNotFoundException,
    FileUploadException,
    BusinessException
)

# Get logger
logger = get_logger(__name__)

# Create router
router = APIRouter(
    prefix="/api",
    tags=["upload"],
    dependencies=[Depends(get_current_user)]
)

# Initialize upload service
upload_service = UploadService()


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    file: UploadFile = File(..., description="要上传的文件"),
    context: str = Form(..., description="上传上下文 (avatar, app_icon, knowledge_base, temp, attachment)"),
    metadata: Optional[str] = Form(None, description="文件元数据 (JSON格式)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    单文件上传接口
    
    - **file**: 要上传的文件
    - **context**: 上传上下文，决定文件存储位置和验证规则
    - **metadata**: 可选的文件元数据，JSON格式字符串
    
    返回上传成功的文件信息
    """
    logger.info(f"Upload request: filename={file.filename}, context={context}, user={current_user.id}")
    
    try:
        # Validate and parse context
        try:
            upload_context = UploadContext(context)
        except ValueError:
            logger.warning(f"Invalid upload context: {context}")
            raise ValidationException(
                f"无效的上传上下文: {context}. 允许的值: {', '.join([c.value for c in UploadContext])}",
                field="context"
            )
        
        # Parse metadata if provided
        file_metadata = {}
        if metadata:
            try:
                file_metadata = json.loads(metadata)
            except json.JSONDecodeError:
                logger.warning(f"Invalid metadata JSON: {metadata}")
                raise ValidationException(
                    "元数据必须是有效的JSON格式",
                    field="metadata"
                )
        
        # Upload file
        db_file = upload_service.upload_file(
            file=file,
            context=upload_context,
            metadata=file_metadata,
            current_user=current_user,
            db=db
        )
        
        # Convert to response schema
        file_response = GenericFileResponse.model_validate(db_file)
        
        logger.info(f"Upload successful: {file.filename} (ID: {db_file.id})")
        return success(data=file_response.dict(), msg="文件上传成功")
        
    except BusinessException:
        # Business exceptions are handled by global exception handlers
        raise
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        # Wrap unknown exceptions as FileUploadException
        raise FileUploadException(
            f"文件上传失败: {str(e)}",
            cause=e
        )


@router.post("/upload/batch", response_model=ApiResponse)
async def upload_files_batch(
    files: List[UploadFile] = File(..., description="要上传的文件列表"),
    context: str = Form(..., description="上传上下文 (avatar, app_icon, knowledge_base, temp, attachment)"),
    metadata: Optional[str] = Form(None, description="文件元数据 (JSON格式)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    批量文件上传接口
    
    - **files**: 要上传的文件列表（最多20个）
    - **context**: 上传上下文，决定文件存储位置和验证规则
    - **metadata**: 可选的文件元数据，JSON格式字符串，应用于所有文件
    
    返回每个文件的上传结果
    """
    logger.info(f"Batch upload request: {len(files)} files, context={context}, user={current_user.id}")
    
    try:
        # Validate and parse context
        try:
            upload_context = UploadContext(context)
        except ValueError:
            logger.warning(f"Invalid upload context: {context}")
            raise ValidationException(
                f"无效的上传上下文: {context}. 允许的值: {', '.join([c.value for c in UploadContext])}",
                field="context"
            )
        
        # Parse metadata if provided
        file_metadata = {}
        if metadata:
            try:
                file_metadata = json.loads(metadata)
            except json.JSONDecodeError:
                logger.warning(f"Invalid metadata JSON: {metadata}")
                raise ValidationException(
                    "元数据必须是有效的JSON格式",
                    field="metadata"
                )
        
        # Upload files in batch
        upload_results = upload_service.upload_files_batch(
            files=files,
            context=upload_context,
            metadata=file_metadata,
            current_user=current_user,
            db=db
        )
        
        # Convert results to response schemas
        result_schemas = []
        for result in upload_results:
            result_schema = UploadResultSchema(
                success=result.success,
                file_id=result.file_id,
                file_name=result.file_name,
                error=result.error,
                file_info=None
            )
            
            # If upload was successful, get file info
            if result.success and result.file_id:
                try:
                    db_file = upload_service.get_file(result.file_id, current_user, db)
                    result_schema.file_info = GenericFileResponse.model_validate(db_file)
                except Exception as e:
                    logger.warning(f"Failed to get file info for {result.file_id}: {str(e)}")
            
            result_schemas.append(result_schema)
        
        # Create batch response
        batch_response = BatchUploadResponse(
            total=len(files),
            success_count=sum(1 for r in upload_results if r.success),
            failed_count=sum(1 for r in upload_results if not r.success),
            results=result_schemas
        )
        
        logger.info(f"Batch upload completed: {batch_response.success_count}/{batch_response.total} successful")
        return success(data=batch_response.dict(), msg="批量上传完成")
        
    except BusinessException:
        # Business exceptions are handled by global exception handlers
        raise
    except Exception as e:
        logger.error(f"Batch upload failed: {str(e)}")
        # Wrap unknown exceptions as FileUploadException
        raise FileUploadException(
            f"批量上传失败: {str(e)}",
            cause=e
        )


@router.get("/files/{file_id}", response_model=Any)
async def download_file(
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    文件下载接口
    
    - **file_id**: 文件ID
    
    返回文件内容供下载
    """
    logger.info(f"Download request: file_id={file_id}, user={current_user.id}")
    
    try:
        # Parse file_id
        import uuid
        try:
            file_uuid = uuid.UUID(file_id)
        except ValueError:
            logger.warning(f"Invalid file ID format: {file_id}")
            raise ValidationException(
                "无效的文件ID格式",
                field="file_id"
            )
        
        # Get file from database
        db_file = upload_service.get_file(file_uuid, current_user, db)
        
        # Check if physical file exists
        storage_path = Path(db_file.storage_path)
        if not storage_path.exists():
            logger.error(f"Physical file not found: {storage_path}")
            raise ResourceNotFoundException(
                "文件",
                str(file_uuid),
                context={"detail": "文件未找到（可能已被删除）"}
            )
        
        # Return file response
        logger.info(f"Download successful: {db_file.file_name} (ID: {file_id})")
        return FileResponse(
            path=str(storage_path),
            filename=db_file.file_name,
            media_type=db_file.mime_type or "application/octet-stream"
        )
        
    except BusinessException:
        # Business exceptions are handled by global exception handlers
        raise
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        # Wrap unknown exceptions
        raise FileUploadException(
            f"文件下载失败: {str(e)}",
            cause=e
        )


@router.delete("/files/{file_id}", response_model=ApiResponse)
async def delete_file(
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    文件删除接口
    
    - **file_id**: 文件ID
    
    删除文件（包括物理文件和数据库记录）
    """
    logger.info(f"Delete request: file_id={file_id}, user={current_user.id}")
    
    try:
        # Parse file_id
        import uuid
        try:
            file_uuid = uuid.UUID(file_id)
        except ValueError:
            logger.warning(f"Invalid file ID format: {file_id}")
            raise ValidationException(
                "无效的文件ID格式",
                field="file_id"
            )
        
        # Delete file
        upload_service.delete_file(file_uuid, current_user, db)
        
        logger.info(f"Delete successful: file_id={file_id}")
        return success(msg="文件删除成功")
        
    except BusinessException:
        # Business exceptions are handled by global exception handlers
        raise
    except Exception as e:
        logger.error(f"Delete failed: {str(e)}")
        # Wrap unknown exceptions
        raise FileUploadException(
            f"文件删除失败: {str(e)}",
            cause=e
        )


@router.put("/files/{file_id}", response_model=ApiResponse)
async def update_file_metadata(
    file_id: str,
    update_data: FileMetadataUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    文件元数据更新接口
    
    - **file_id**: 文件ID
    - **update_data**: 要更新的元数据
    
    更新文件的元数据（文件名、自定义元数据、公开状态）
    """
    logger.info(f"Update metadata request: file_id={file_id}, user={current_user.id}")
    
    try:
        # Parse file_id
        import uuid
        try:
            file_uuid = uuid.UUID(file_id)
        except ValueError:
            logger.warning(f"Invalid file ID format: {file_id}")
            raise ValidationException(
                "无效的文件ID格式",
                field="file_id"
            )
        
        # Convert update data to dict, excluding unset fields
        update_dict = update_data.dict(exclude_unset=True)
        
        if not update_dict:
            logger.warning(f"No fields to update for file: {file_id}")
            raise ValidationException(
                "没有提供要更新的字段",
                field="update_data"
            )
        
        # Update file metadata
        updated_file = upload_service.update_file_metadata(
            file_uuid, update_dict, current_user, db
        )
        
        # Convert to response schema
        file_response = GenericFileResponse.model_validate(updated_file)
        
        logger.info(f"Update metadata successful: file_id={file_id}")
        return success(data=file_response.dict(), msg="文件元数据更新成功")
        
    except BusinessException:
        # Business exceptions are handled by global exception handlers
        raise
    except Exception as e:
        logger.error(f"Update metadata failed: {str(e)}")
        # Wrap unknown exceptions
        raise FileUploadException(
            f"文件元数据更新失败: {str(e)}",
            cause=e
        )
