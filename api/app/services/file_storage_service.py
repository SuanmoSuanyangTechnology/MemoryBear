"""
File storage service module.

This module provides a high-level service layer for file storage operations,
encapsulating the storage backend and providing file_key generation, logging,
and error handling.
"""

import logging
import time
import uuid
from typing import Optional

from app.core.storage import StorageFactory, StorageBackend
from app.core.storage_exceptions import (
    StorageError,
    StorageUploadError,
    StorageDownloadError,
    StorageDeleteError,
)
from app.core.logging_config import get_business_logger

# Obtain a dedicated logger for business logic
logger = get_business_logger()


def generate_file_key(
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    file_id: uuid.UUID,
    file_ext: str,
) -> str:
    """
    Generate a unique file key for storage.

    The file key follows the format: {tenant_id}/{workspace_id}/{file_id}{file_ext}

    Args:
        tenant_id: The tenant UUID.
        workspace_id: The workspace UUID.
        file_id: The file UUID.
        file_ext: The file extension (e.g., '.pdf', '.txt').

    Returns:
        A unique file key string.

    Example:
        >>> generate_file_key(
        ...     uuid.UUID('550e8400-e29b-41d4-a716-446655440000'),
        ...     uuid.UUID('660e8400-e29b-41d4-a716-446655440001'),
        ...     uuid.UUID('770e8400-e29b-41d4-a716-446655440002'),
        ...     '.pdf'
        ... )
        '550e8400-e29b-41d4-a716-446655440000/660e8400-e29b-41d4-a716-446655440001/770e8400-e29b-41d4-a716-446655440002.pdf'
    """
    # Ensure file_ext starts with a dot
    if file_ext and not file_ext.startswith('.'):
        file_ext = f'.{file_ext}'
    
    return f"{tenant_id}/{workspace_id}/{file_id}{file_ext}"


class FileStorageService:
    """
    High-level service for file storage operations.

    This service encapsulates the storage backend and provides:
    - File key generation
    - Upload, download, delete operations
    - Comprehensive logging
    - Error handling with meaningful messages
    """

    def __init__(self, storage: Optional[StorageBackend] = None):
        """
        Initialize the file storage service.

        Args:
            storage: Optional storage backend instance. If not provided,
                     the default storage backend from StorageFactory is used.
        """
        self._storage = storage

    @property
    def storage(self) -> StorageBackend:
        """
        Get the storage backend instance (lazy initialization).

        Returns:
            The storage backend instance.
        """
        if self._storage is None:
            self._storage = StorageFactory.get_storage()
        return self._storage

    async def upload_file(
        self,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        file_id: uuid.UUID,
        file_ext: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to storage.

        Args:
            tenant_id: The tenant UUID.
            workspace_id: The workspace UUID.
            file_id: The file UUID.
            file_ext: The file extension.
            content: The file content as bytes.
            content_type: Optional MIME type of the file.

        Returns:
            The file key of the uploaded file.

        Raises:
            StorageUploadError: If the upload operation fails.
        """
        file_key = generate_file_key(tenant_id, workspace_id, file_id, file_ext)
        start_time = time.time()

        logger.info(
            f"Starting file upload: file_key={file_key}, "
            f"size={len(content)} bytes, content_type={content_type}"
        )

        try:
            await self.storage.upload(file_key, content, content_type)
            elapsed_time = time.time() - start_time

            logger.info(
                f"File upload successful: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s"
            )

            return file_key

        except StorageError as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"File upload failed: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s, error={str(e)}"
            )
            raise StorageUploadError(
                message=f"Failed to upload file: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Unexpected error during file upload: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s, error={str(e)}"
            )
            raise StorageUploadError(
                message=f"Unexpected error during upload: {str(e)}",
                file_key=file_key,
                cause=e,
            )

    async def download_file(self, file_key: str) -> bytes:
        """
        Download a file from storage.

        Args:
            file_key: The file key of the file to download.

        Returns:
            The file content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
            StorageDownloadError: If the download operation fails.
        """
        start_time = time.time()

        logger.info(f"Starting file download: file_key={file_key}")

        try:
            content = await self.storage.download(file_key)
            elapsed_time = time.time() - start_time

            logger.info(
                f"File download successful: file_key={file_key}, "
                f"size={len(content)} bytes, elapsed_time={elapsed_time:.3f}s"
            )

            return content

        except FileNotFoundError:
            elapsed_time = time.time() - start_time
            logger.warning(
                f"File not found: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s"
            )
            raise
        except StorageError as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"File download failed: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s, error={str(e)}"
            )
            raise StorageDownloadError(
                message=f"Failed to download file: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Unexpected error during file download: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s, error={str(e)}"
            )
            raise StorageDownloadError(
                message=f"Unexpected error during download: {str(e)}",
                file_key=file_key,
                cause=e,
            )

    async def delete_file(self, file_key: str) -> bool:
        """
        Delete a file from storage.

        Args:
            file_key: The file key of the file to delete.

        Returns:
            True if the file was deleted, False if it didn't exist.

        Raises:
            StorageDeleteError: If the delete operation fails.
        """
        start_time = time.time()

        logger.info(f"Starting file deletion: file_key={file_key}")

        try:
            result = await self.storage.delete(file_key)
            elapsed_time = time.time() - start_time

            if result:
                logger.info(
                    f"File deletion successful: file_key={file_key}, "
                    f"elapsed_time={elapsed_time:.3f}s"
                )
            else:
                logger.info(
                    f"File did not exist: file_key={file_key}, "
                    f"elapsed_time={elapsed_time:.3f}s"
                )

            return result

        except StorageError as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"File deletion failed: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s, error={str(e)}"
            )
            raise StorageDeleteError(
                message=f"Failed to delete file: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Unexpected error during file deletion: file_key={file_key}, "
                f"elapsed_time={elapsed_time:.3f}s, error={str(e)}"
            )
            raise StorageDeleteError(
                message=f"Unexpected error during deletion: {str(e)}",
                file_key=file_key,
                cause=e,
            )

    async def file_exists(self, file_key: str) -> bool:
        """
        Check if a file exists in storage.

        Args:
            file_key: The file key to check.

        Returns:
            True if the file exists, False otherwise.
        """
        logger.debug(f"Checking file existence: file_key={file_key}")

        try:
            exists = await self.storage.exists(file_key)
            logger.debug(f"File existence check: file_key={file_key}, exists={exists}")
            return exists
        except Exception as e:
            logger.error(
                f"Error checking file existence: file_key={file_key}, error={str(e)}"
            )
            raise

    async def get_file_url(self, file_key: str, expires: int = 3600) -> str:
        """
        Get an access URL for a file.

        Args:
            file_key: The file key.
            expires: URL validity period in seconds (default: 1 hour).

        Returns:
            URL for accessing the file.
        """
        logger.debug(f"Getting file URL: file_key={file_key}, expires={expires}s")

        try:
            url = await self.storage.get_url(file_key, expires)
            logger.debug(f"File URL generated: file_key={file_key}")
            return url
        except Exception as e:
            logger.error(
                f"Error getting file URL: file_key={file_key}, error={str(e)}"
            )
            raise


# Create a default instance for convenience
_default_service: Optional[FileStorageService] = None


def get_file_storage_service() -> FileStorageService:
    """
    Get the default file storage service instance.

    Returns:
        The default FileStorageService instance.
    """
    global _default_service
    if _default_service is None:
        _default_service = FileStorageService()
    return _default_service
