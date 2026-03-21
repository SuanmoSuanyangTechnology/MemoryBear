"""
Local file system storage backend implementation.

This module provides a storage backend that stores files on the local
file system using async I/O operations via aiofiles.
"""

import logging
from pathlib import Path
from typing import Optional

import aiofiles
import aiofiles.os

from app.core.storage.base import StorageBackend
from app.core.storage_exceptions import (
    StorageDeleteError,
    StorageDownloadError,
    StorageUploadError,
)

logger = logging.getLogger(__name__)


class LocalStorage(StorageBackend):
    """
    Local file system storage implementation.

    This class implements the StorageBackend interface for storing files
    on the local file system. It uses aiofiles for async file operations.

    Attributes:
        base_path: The base directory path where files will be stored.
    """

    def __init__(self, base_path: str):
        """
        Initialize the LocalStorage backend.

        Args:
            base_path: The base directory path for file storage.
                      Will be created if it doesn't exist.
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized with base_path: {self.base_path}")

    def _get_full_path(self, file_key: str) -> Path:
        """
        Get the full file system path for a given file key.

        Args:
            file_key: The unique identifier for the file.

        Returns:
            The full Path object for the file.
        """
        return self.base_path / file_key

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to the local file system.

        Args:
            file_key: Unique identifier for the file in the storage system.
            content: File content as bytes.
            content_type: Optional MIME type (not used for local storage).

        Returns:
            The full path of the uploaded file as a string.

        Raises:
            StorageUploadError: If the upload operation fails.
        """
        full_path = self._get_full_path(file_key)

        try:
            # Create parent directories if they don't exist
            full_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(full_path, "wb") as f:
                await f.write(content)

            logger.info(f"File uploaded successfully: {file_key}")
            return str(full_path)

        except Exception as e:
            logger.error(f"Failed to upload file {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file: {e}",
                file_key=file_key,
                cause=e,
            )

    async def download(self, file_key: str) -> bytes:
        """
        Download a file from the local file system.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            File content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
            StorageDownloadError: If the download operation fails.
        """
        full_path = self._get_full_path(file_key)

        if not full_path.exists():
            logger.warning(f"File not found: {file_key}")
            raise FileNotFoundError(f"File not found: {file_key}")

        try:
            async with aiofiles.open(full_path, "rb") as f:
                content = await f.read()

            logger.info(f"File downloaded successfully: {file_key}")
            return content

        except FileNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to download file {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file: {e}",
                file_key=file_key,
                cause=e,
            )

    async def delete(self, file_key: str) -> bool:
        """
        Delete a file from the local file system.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file was deleted, False if it didn't exist.

        Raises:
            StorageDeleteError: If the delete operation fails.
        """
        full_path = self._get_full_path(file_key)

        if not full_path.exists():
            logger.info(f"File does not exist, nothing to delete: {file_key}")
            return False

        try:
            await aiofiles.os.remove(full_path)
            logger.info(f"File deleted successfully: {file_key}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete file {file_key}: {e}")
            raise StorageDeleteError(
                message=f"Failed to delete file: {e}",
                file_key=file_key,
                cause=e,
            )

    async def exists(self, file_key: str) -> bool:
        """
        Check if a file exists in the local file system.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file exists, False otherwise.
        """
        full_path = self._get_full_path(file_key)
        return full_path.exists()

    async def get_url(self, file_key: str, expires: int = 3600) -> str:
        """
        Get an access URL for the file.

        For local storage, this returns a relative path that can be used
        by the API layer to serve the file.

        Args:
            file_key: Unique identifier for the file in the storage system.
            expires: URL validity period in seconds (not used for local storage).

        Returns:
            A relative URL path for accessing the file.
        """
        return f"/files/{file_key}"
