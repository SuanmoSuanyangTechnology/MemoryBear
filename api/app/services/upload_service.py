"""
Upload Service for Generic File Upload System
Handles file upload, storage, access, deletion, and metadata updates.
"""
import os
import uuid
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import UploadFile

from app.models.user_model import User
from app.models.generic_file_model import GenericFile
from app.repositories.generic_file_repository import GenericFileRepository
from app.core.upload_enums import UploadContext
from app.core.storage_strategy import StrategyFactory
from app.core.validators.file_validator import FileValidator
from app.core.exceptions import BusinessException, PermissionDeniedException
from app.core.error_codes import BizCode
from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.uow import IUnitOfWork
from app.core.compensation import CompensationHandler

# Get logger
logger = get_logger(__name__)


class FileNotFoundError(BusinessException):
    """Exception raised when file is not found."""
    def __init__(self, file_id: uuid.UUID):
        super().__init__(
            f"文件 {file_id} 不存在",
            code=BizCode.NOT_FOUND
        )


class FileAccessDeniedError(BusinessException):
    """Exception raised when file access is denied."""
    def __init__(self, file_id: uuid.UUID):
        super().__init__(
            f"无权访问文件 {file_id}",
            code=BizCode.FORBIDDEN
        )


class FileStorageError(BusinessException):
    """Exception raised when file storage fails."""
    def __init__(self, reason: str):
        super().__init__(
            f"文件存储失败: {reason}",
            code=BizCode.INTERNAL_ERROR
        )


class FileReferencedError(BusinessException):
    """Exception raised when trying to delete a referenced file."""
    def __init__(self, file_id: uuid.UUID, reference_count: int):
        super().__init__(
            f"文件 {file_id} 被 {reference_count} 个资源引用，无法删除",
            code=BizCode.BAD_REQUEST
        )


class UploadResult:
    """Result of a file upload operation."""
    def __init__(self, success: bool, file_id: Optional[uuid.UUID] = None, 
                 file_name: str = "", error: Optional[str] = None):
        self.success = success
        self.file_id = file_id
        self.file_name = file_name
        self.error = error


class UploadService:
    """
    Service for handling file uploads and management.
    Coordinates validation, storage, and database operations.
    Uses Unit of Work pattern for transaction management.
    """
    
    def __init__(self, uow: IUnitOfWork = None):
        self.validator = FileValidator()
        self.uow = uow
    
    def upload_file(
        self,
        file: UploadFile,
        context: UploadContext,
        metadata: Optional[Dict[str, Any]],
        current_user: User,
        db: Session = None
    ) -> GenericFile:
        """
        Upload a single file using Unit of Work pattern with compensation transactions.
        
        Args:
            file: The uploaded file
            context: Upload context (avatar, app_icon, etc.)
            metadata: Additional metadata for the file
            current_user: The user uploading the file
            db: Optional database session (for backward compatibility)
            
        Returns:
            GenericFile: The created file record
            
        Raises:
            FileSizeExceededError: If file size exceeds limit
            FileTypeNotAllowedError: If file type is not allowed
            EmptyFileError: If file is empty
            FileStorageError: If file storage fails
        """
        logger.info(f"Starting file upload: filename={file.filename}, context={context}, user={current_user.id}")
        
        if metadata is None:
            metadata = {}
        
        # Get storage strategy for this context
        strategy = StrategyFactory.get_strategy(context)
        upload_policy = strategy.get_upload_policy()
        
        # Validate file against upload policy
        logger.debug(f"Validating file: {file.filename}")
        self.validator.validate_and_raise(file, upload_policy)
        
        # Generate file ID
        file_id = uuid.uuid4()
        
        # Extract file information
        filename = file.filename or "unknown"
        file_extension = ""
        if "." in filename:
            file_extension = "." + filename.rsplit(".", 1)[1].lower()
        
        # Get file size
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
        
        # Get storage path
        storage_path = strategy.get_storage_path(
            tenant_id=current_user.tenant_id,
            file_id=file_id,
            file_extension=file_extension,
            metadata=metadata
        )
        
        logger.debug(f"Storage path: {storage_path}")
        
        # Use Unit of Work pattern with compensation handler
        compensation = CompensationHandler()
        
        try:
            # Use provided UoW or create a new one for backward compatibility
            if self.uow:
                uow = self.uow
                should_manage_context = False
            else:
                # Backward compatibility: use provided db session
                if db:
                    # Create a temporary UoW wrapper for the existing session
                    from app.core.uow import SqlAlchemyUnitOfWork
                    uow = SqlAlchemyUnitOfWork(lambda: db)
                    uow._session = db
                    uow.files = GenericFileRepository(db)
                    should_manage_context = False
                else:
                    raise FileStorageError("Either uow or db session must be provided")
            
            # 1. Save physical file
            self._save_physical_file(file, storage_path)
            
            # Register compensation: delete physical file if database operation fails
            compensation.register(lambda: self._delete_physical_file(storage_path))
            
            # 2. Generate access URL
            access_url = None
            if context in [UploadContext.AVATAR, UploadContext.APP_ICON]:
                access_url = f"{settings.FILE_ACCESS_URL_PREFIX}/{file_id}"
            
            # 3. Create file data
            file_data = {
                "id": file_id,
                "tenant_id": current_user.tenant_id,
                "created_by": current_user.id,
                "file_name": filename,
                "file_ext": file_extension,
                "file_size": file_size,
                "mime_type": file.content_type,
                "context": context.value,
                "storage_path": str(storage_path),
                "file_metadata": metadata,
                "status": "active",
                "is_public": metadata.get("is_public", False),
                "access_url": access_url,
                "reference_count": 0,
            }
            
            # 4. Create database record
            db_file = uow.files.create_file(file_data)
            
            # 5. Commit transaction (only if we're managing the session)
            if should_manage_context:
                uow.commit()
            elif db:
                db.commit()
            
            # Success - clear compensation operations
            compensation.clear()
            
            logger.info(f"File upload completed successfully: {filename} (ID: {file_id})")
            return db_file
            
        except Exception as e:
            # Execute compensation operations
            compensation.execute()
            
            # Rollback if we're managing the session
            if db:
                db.rollback()
            
            logger.error(f"File upload failed: {str(e)}")
            raise FileStorageError(f"文件上传失败: {str(e)}")
    
    def _save_physical_file(self, file: UploadFile, storage_path: Path):
        """
        Save physical file to filesystem.
        
        Args:
            file: The uploaded file
            storage_path: Path where file should be saved
            
        Raises:
            FileStorageError: If file save fails
        """
        try:
            # Create directory if it doesn't exist
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save file
            with open(storage_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            logger.info(f"File saved to filesystem: {storage_path}")
            
        except Exception as e:
            logger.error(f"Failed to save file to filesystem: {str(e)}")
            raise FileStorageError(f"无法保存文件到磁盘: {str(e)}")
    
    def _delete_physical_file(self, storage_path: Path):
        """
        Delete physical file (compensation operation).
        
        Args:
            storage_path: Path of file to delete
        """
        try:
            if os.path.exists(storage_path):
                os.remove(storage_path)
                logger.info(f"补偿操作：删除文件 {storage_path}")
        except Exception as e:
            logger.error(f"删除文件失败: {e}")
    
    def _restore_file_from_backup(self, backup_path: Path, original_path: Path):
        """
        Restore file from backup (compensation operation).
        
        Args:
            backup_path: Path of backup file
            original_path: Path where file should be restored
        """
        try:
            if backup_path.exists():
                shutil.copy2(backup_path, original_path)
                logger.info(f"补偿操作：从备份恢复文件 {original_path}")
                # Clean up backup after restoration
                os.remove(backup_path)
                logger.debug(f"补偿操作：删除备份文件 {backup_path}")
        except Exception as e:
            logger.error(f"恢复文件失败: {e}")

    def upload_files_batch(
        self,
        files: List[UploadFile],
        context: UploadContext,
        metadata: Optional[Dict[str, Any]],
        current_user: User,
        db: Session = None
    ) -> List[UploadResult]:
        """
        Upload multiple files in batch.
        Individual file failures do not affect other files.
        
        Args:
            files: List of uploaded files
            context: Upload context (avatar, app_icon, etc.)
            metadata: Additional metadata for the files
            current_user: The user uploading the files
            db: Optional database session (for backward compatibility)
            
        Returns:
            List[UploadResult]: List of upload results for each file
            
        Raises:
            BusinessException: If batch size exceeds limit
        """
        logger.info(f"Starting batch upload: {len(files)} files, context={context}, user={current_user.id}")
        
        # Validate batch size
        MAX_BATCH_SIZE = 20
        if len(files) > MAX_BATCH_SIZE:
            raise BusinessException(
                f"批量上传文件数量不能超过 {MAX_BATCH_SIZE} 个",
                code=BizCode.BAD_REQUEST,
                context={
                    "file_count": len(files),
                    "max_batch_size": MAX_BATCH_SIZE,
                    "user_id": str(current_user.id),
                    "tenant_id": str(current_user.tenant_id),
                    "context": context
                }
            )
        
        results = []
        
        for file in files:
            try:
                # Upload each file independently
                db_file = self.upload_file(file, context, metadata, current_user, db)
                
                results.append(UploadResult(
                    success=True,
                    file_id=db_file.id,
                    file_name=file.filename or "unknown",
                    error=None
                ))
                
                logger.info(f"Batch upload success: {file.filename}")
                
            except Exception as e:
                # Log error but continue with other files
                logger.error(f"Batch upload failed for {file.filename}: {str(e)}")
                
                results.append(UploadResult(
                    success=False,
                    file_id=None,
                    file_name=file.filename or "unknown",
                    error=str(e)
                ))
        
        logger.info(f"Batch upload completed: {sum(1 for r in results if r.success)}/{len(files)} successful")
        return results
    
    def get_file(
        self,
        file_id: uuid.UUID,
        current_user: User,
        db: Session = None
    ) -> GenericFile:
        """
        Get a file by ID with permission validation.
        
        Args:
            file_id: UUID of the file
            current_user: The user requesting the file
            db: Optional database session (for backward compatibility)
            
        Returns:
            GenericFile: The file record
            
        Raises:
            FileNotFoundError: If file doesn't exist
            FileAccessDeniedError: If user doesn't have permission
        """
        logger.debug(f"Getting file: file_id={file_id}, user={current_user.id}")
        
        # Use UoW or provided db session
        if self.uow:
            with self.uow:
                file = self.uow.files.get_file_by_id(file_id)
        elif db:
            repository = GenericFileRepository(db)
            file = repository.get_file_by_id(file_id)
        else:
            raise FileStorageError("Either uow or db session must be provided")
        
        if not file:
            logger.warning(f"File not found: {file_id}")
            raise FileNotFoundError(file_id)
        
        # Check permissions using permission service
        from app.core.permissions import permission_service, Subject, Resource, Action
        
        subject = Subject.from_user(current_user)
        resource = Resource.from_file(file)
        
        try:
            permission_service.require_permission(
                subject,
                Action.READ,
                resource,
                error_message=f"无权访问文件 {file_id}"
            )
        except PermissionDeniedException:
            logger.warning(f"Access denied: file_id={file_id}, user={current_user.id}")
            raise FileAccessDeniedError(file_id)
        
        logger.debug(f"File access granted: {file.file_name}")
        return file
    
    def delete_file(
        self,
        file_id: uuid.UUID,
        current_user: User,
        db: Session = None
    ) -> None:
        """
        Delete a file (both physical file and database record) using UoW pattern with compensation.
        
        This method uses compensation transactions to ensure data consistency:
        1. Delete physical file first
        2. Register compensation to restore file if DB deletion fails
        3. Delete database record
        4. Commit transaction
        5. Clear compensation on success
        
        Args:
            file_id: UUID of the file to delete
            current_user: The user requesting deletion
            db: Optional database session (for backward compatibility)
            
        Raises:
            FileNotFoundError: If file doesn't exist
            FileAccessDeniedError: If user doesn't have permission
            FileReferencedError: If file is still referenced
            FileStorageError: If deletion fails
        """
        logger.info(f"Deleting file: file_id={file_id}, user={current_user.id}")
        
        # Get file and check permissions
        if self.uow:
            with self.uow:
                file = self.uow.files.get_file_by_id(file_id)
        elif db:
            repository = GenericFileRepository(db)
            file = repository.get_file_by_id(file_id)
        else:
            raise FileStorageError("Either uow or db session must be provided")
        
        if not file:
            logger.warning(f"File not found for deletion: {file_id}")
            raise FileNotFoundError(file_id)
        
        # Check permissions using permission service
        from app.core.permissions import permission_service, Subject, Resource, Action
        
        subject = Subject.from_user(current_user)
        resource = Resource.from_file(file)
        
        try:
            permission_service.require_permission(
                subject,
                Action.DELETE,
                resource,
                error_message=f"无权删除文件 {file_id}"
            )
        except PermissionDeniedException:
            logger.warning(f"Delete access denied: file_id={file_id}, user={current_user.id}")
            raise FileAccessDeniedError(file_id)
        
        # Check reference count
        if file.reference_count > 0:
            logger.warning(f"Cannot delete referenced file: file_id={file_id}, references={file.reference_count}")
            raise FileReferencedError(file_id, file.reference_count)
        
        # Store storage path and file content for potential restoration
        storage_path = Path(file.storage_path)
        backup_path = None
        
        # Use compensation handler for atomic deletion
        compensation = CompensationHandler()
        
        try:
            # 1. Backup and delete physical file first
            if storage_path.exists():
                # Create backup in temp location
                backup_path = storage_path.parent / f".backup_{file_id}{storage_path.suffix}"
                shutil.copy2(storage_path, backup_path)
                logger.debug(f"Created backup: {backup_path}")
                
                # Delete original file
                os.remove(storage_path)
                logger.info(f"Physical file deleted: {storage_path}")
                
                # Register compensation: restore file from backup if DB deletion fails
                compensation.register(lambda: self._restore_file_from_backup(backup_path, storage_path))
            else:
                logger.warning(f"Physical file not found: {storage_path}")
            
            # 2. Delete database record (soft delete)
            if self.uow:
                with self.uow:
                    self.uow.files.delete_file(file_id)
                    self.uow.commit()
            elif db:
                repository = GenericFileRepository(db)
                repository.delete_file(file_id)
                db.commit()
            
            logger.info(f"File record deleted successfully: {file.file_name} (ID: {file_id})")
            
            # 3. Success - clear compensations and remove backup
            compensation.clear()
            if backup_path and backup_path.exists():
                os.remove(backup_path)
                logger.debug(f"Removed backup: {backup_path}")
            
        except Exception as e:
            # Execute compensation to restore file
            compensation.execute()
            
            # Rollback database if using db session
            if db:
                db.rollback()
            
            logger.error(f"Failed to delete file: {str(e)}")
            raise FileStorageError(f"无法删除文件: {str(e)}")
    
    def update_file_metadata(
        self,
        file_id: uuid.UUID,
        update_data: Dict[str, Any],
        current_user: User,
        db: Session = None
    ) -> GenericFile:
        """
        Update file metadata using UoW pattern.
        
        Args:
            file_id: UUID of the file to update
            update_data: Dictionary containing fields to update
            current_user: The user requesting the update
            db: Optional database session (for backward compatibility)
            
        Returns:
            GenericFile: The updated file record
            
        Raises:
            FileNotFoundError: If file doesn't exist
            FileAccessDeniedError: If user doesn't have permission
        """
        logger.info(f"Updating file metadata: file_id={file_id}, user={current_user.id}")
        
        # Get file and check permissions
        if self.uow:
            with self.uow:
                file = self.uow.files.get_file_by_id(file_id)
        elif db:
            repository = GenericFileRepository(db)
            file = repository.get_file_by_id(file_id)
        else:
            raise FileStorageError("Either uow or db session must be provided")
        
        if not file:
            logger.warning(f"File not found for update: {file_id}")
            raise FileNotFoundError(file_id)
        
        # Check permissions using permission service
        from app.core.permissions import permission_service, Subject, Resource, Action
        
        subject = Subject.from_user(current_user)
        resource = Resource.from_file(file)
        
        try:
            permission_service.require_permission(
                subject,
                Action.UPDATE,
                resource,
                error_message=f"无权更新文件 {file_id}"
            )
        except PermissionDeniedException:
            logger.warning(f"Update access denied: file_id={file_id}, user={current_user.id}")
            raise FileAccessDeniedError(file_id)
        
        # Filter allowed fields for update
        # Users can only update: file_name, file_metadata, is_public
        allowed_fields = ["file_name", "file_metadata", "is_public"]
        filtered_update_data = {
            key: value for key, value in update_data.items()
            if key in allowed_fields
        }
        
        if not filtered_update_data:
            logger.warning(f"No valid fields to update for file: {file_id}")
            return file
        
        # Update file metadata
        try:
            if self.uow:
                with self.uow:
                    updated_file = self.uow.files.update_file(file_id, filtered_update_data)
                    self.uow.commit()
            elif db:
                repository = GenericFileRepository(db)
                updated_file = repository.update_file(file_id, filtered_update_data)
                db.commit()
            
            logger.info(f"File metadata updated successfully: {file.file_name} (ID: {file_id})")
            return updated_file
            
        except Exception as e:
            if db:
                db.rollback()
            logger.error(f"Failed to update file metadata: {str(e)}")
            raise FileStorageError(f"无法更新文件元数据: {str(e)}")
