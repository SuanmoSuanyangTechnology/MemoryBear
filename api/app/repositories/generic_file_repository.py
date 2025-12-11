"""
Generic File Repository
Handles database operations for generic file uploads.
"""
import uuid
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from app.models.generic_file_model import GenericFile
from app.core.upload_enums import UploadContext
from app.core.logging_config import get_db_logger

# Get database logger
db_logger = get_db_logger()


class GenericFileRepository:
    """Repository for generic file operations"""

    def __init__(self, db: Session):
        self.db = db

    def create_file(self, file_data: Dict[str, Any]) -> GenericFile:
        """
        Create a new file record in the database.
        
        Args:
            file_data: Dictionary containing file information
            
        Returns:
            GenericFile: Created file record
            
        Raises:
            Exception: If database operation fails
        """
        db_logger.debug(f"Creating file record: filename={file_data.get('file_name')}")
        
        try:
            db_file = GenericFile(**file_data)
            self.db.add(db_file)
            self.db.flush()
            db_logger.info(f"File record created successfully: {file_data.get('file_name')} (ID: {db_file.id})")
            return db_file
        except Exception as e:
            db_logger.error(f"Failed to create file record: filename={file_data.get('file_name')} - {str(e)}")
            raise

    def get_file_by_id(self, file_id: uuid.UUID) -> Optional[GenericFile]:
        """
        Get a file by its ID.
        
        Args:
            file_id: UUID of the file
            
        Returns:
            Optional[GenericFile]: File record if found, None otherwise
        """
        db_logger.debug(f"Querying file by ID: file_id={file_id}")
        
        try:
            file = self.db.query(GenericFile).filter(
                and_(
                    GenericFile.id == file_id,
                    GenericFile.deleted_at.is_(None)
                )
            ).first()
            
            if file:
                db_logger.debug(f"File found: {file.file_name} (ID: {file_id})")
            else:
                db_logger.debug(f"File not found: file_id={file_id}")
            
            return file
        except Exception as e:
            db_logger.error(f"Failed to query file by ID: file_id={file_id} - {str(e)}")
            raise

    def update_file(self, file_id: uuid.UUID, update_data: Dict[str, Any]) -> Optional[GenericFile]:
        """
        Update file metadata.
        
        Args:
            file_id: UUID of the file to update
            update_data: Dictionary containing fields to update
            
        Returns:
            Optional[GenericFile]: Updated file record if found, None otherwise
        """
        db_logger.debug(f"Updating file: file_id={file_id}")
        
        try:
            file = self.get_file_by_id(file_id)
            if not file:
                db_logger.debug(f"File not found for update: file_id={file_id}")
                return None
            
            # Update allowed fields
            for field, value in update_data.items():
                if hasattr(file, field) and field not in ['id', 'created_by', 'created_at', 'tenant_id']:
                    setattr(file, field, value)
            
            # Update timestamp
            file.updated_at = datetime.now()
            
            self.db.flush()
            db_logger.info(f"File updated successfully: {file.file_name} (ID: {file_id})")
            return file
        except Exception as e:
            db_logger.error(f"Failed to update file: file_id={file_id} - {str(e)}")
            raise

    def delete_file(self, file_id: uuid.UUID) -> bool:
        """
        Soft delete a file by setting deleted_at timestamp.
        
        Args:
            file_id: UUID of the file to delete
            
        Returns:
            bool: True if file was deleted, False if not found
        """
        db_logger.debug(f"Soft deleting file: file_id={file_id}")
        
        try:
            file = self.get_file_by_id(file_id)
            if not file:
                db_logger.debug(f"File not found for deletion: file_id={file_id}")
                return False
            
            # Soft delete by setting deleted_at
            file.deleted_at = datetime.now()
            file.status = "deleted"
            file.updated_at = datetime.now()
            
            self.db.flush()
            db_logger.info(f"File soft deleted successfully: {file.file_name} (ID: {file_id})")
            return True
        except Exception as e:
            db_logger.error(f"Failed to delete file: file_id={file_id} - {str(e)}")
            raise

    def get_files_by_context(
        self,
        context: UploadContext,
        tenant_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        status: Optional[str] = "active",
        created_by: Optional[uuid.UUID] = None
    ) -> Tuple[int, List[GenericFile]]:
        """
        Get files by context with pagination.
        
        Args:
            context: Upload context (avatar, app_icon, etc.)
            tenant_id: Tenant ID for isolation
            page: Page number (1-indexed)
            pagesize: Number of items per page
            status: File status filter (default: "active")
            created_by: Optional filter by creator user ID
            
        Returns:
            Tuple[int, List[GenericFile]]: Total count and list of files
        """
        db_logger.debug(
            f"Querying files by context: context={context}, tenant_id={tenant_id}, "
            f"page={page}, pagesize={pagesize}, status={status}"
        )
        
        try:
            query = self.db.query(GenericFile).filter(
                and_(
                    GenericFile.context == context,
                    GenericFile.tenant_id == tenant_id,
                    GenericFile.deleted_at.is_(None)
                )
            )
            
            # Apply status filter
            if status:
                query = query.filter(GenericFile.status == status)
            
            # Apply creator filter
            if created_by:
                query = query.filter(GenericFile.created_by == created_by)
            
            # Get total count
            total = query.count()
            db_logger.debug(f"Total files found: {total}")
            
            # Apply pagination and ordering
            files = query.order_by(GenericFile.created_at.desc()).offset((page - 1) * pagesize).limit(pagesize).all()
            
            db_logger.info(
                f"Files query successful: context={context}, total={total}, "
                f"returned={len(files)}"
            )
            
            return total, files
        except Exception as e:
            db_logger.error(
                f"Failed to query files by context: context={context}, "
                f"tenant_id={tenant_id} - {str(e)}"
            )
            raise


# Convenience functions for backward compatibility
def create_file(db: Session, file_data: Dict[str, Any]) -> GenericFile:
    """Create a new file record"""
    return GenericFileRepository(db).create_file(file_data)


def get_file_by_id(db: Session, file_id: uuid.UUID) -> Optional[GenericFile]:
    """Get a file by its ID"""
    return GenericFileRepository(db).get_file_by_id(file_id)


def update_file(db: Session, file_id: uuid.UUID, update_data: Dict[str, Any]) -> Optional[GenericFile]:
    """Update file metadata"""
    return GenericFileRepository(db).update_file(file_id, update_data)


def delete_file(db: Session, file_id: uuid.UUID) -> bool:
    """Soft delete a file"""
    return GenericFileRepository(db).delete_file(file_id)


def get_files_by_context(
    db: Session,
    context: UploadContext,
    tenant_id: uuid.UUID,
    page: int = 1,
    pagesize: int = 20,
    status: Optional[str] = "active",
    created_by: Optional[uuid.UUID] = None
) -> Tuple[int, List[GenericFile]]:
    """Get files by context with pagination"""
    return GenericFileRepository(db).get_files_by_context(
        context, tenant_id, page, pagesize, status, created_by
    )
