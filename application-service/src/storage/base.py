"""
Abstract base class for storage backends.

This module defines the StorageBackend abstract class that all storage
implementations must inherit from. It provides a unified interface for
file operations across different storage backends.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class StorageBackend(ABC):
    """
    Abstract base class for storage backends.

    All storage implementations (local, OSS, S3, etc.) must inherit from this
    class and implement all abstract methods. All methods are async to support
    non-blocking I/O operations.
    """

    @abstractmethod
    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to the storage backend.

        Args:
            file_key: Unique identifier for the file in the storage system.
            content: File content as bytes.
            content_type: Optional MIME type of the file.

        Returns:
            The storage path or URL of the uploaded file.

        Raises:
            StorageUploadError: If the upload operation fails.
        """
        pass

    @abstractmethod
    async def upload_stream(
        self,
        file_key: str,
        stream: AsyncIterator[bytes],
        content_type: Optional[str] = None,
    ) -> int:
        """
        Upload a file from an async byte stream.

        Args:
            file_key: Unique identifier for the file.
            stream: Async iterator yielding bytes chunks.
            content_type: Optional MIME type of the file.

        Returns:
            Total bytes written.
        """
        pass

    @abstractmethod
    async def download(self, file_key: str) -> bytes:
        """
        Download a file from the storage backend.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            File content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
            StorageDownloadError: If the download operation fails.
        """
        pass

    @abstractmethod
    async def delete(self, file_key: str) -> bool:
        """
        Delete a file from the storage backend.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file was deleted successfully, False if file didn't exist.

        Raises:
            StorageDeleteError: If the delete operation fails.
        """
        pass

    @abstractmethod
    async def exists(self, file_key: str) -> bool:
        """
        Check if a file exists in the storage backend.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file exists, False otherwise.
        """
        pass

    @abstractmethod
    async def get_url(
        self,
        file_key: str,
        expires: int = 3600,
        file_name: Optional[str] = None
    ) -> str:
        """Get an access URL for the file."""
        pass

    async def get_permanent_url(self, file_key: str) -> Optional[str]:
        """
        Get a permanent public URL for the file (no expiration).

        Returns None by default; remote storage backends should override this
        if the bucket is configured for public read access.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            A permanent public URL, or None if not supported.
        """
        return None
