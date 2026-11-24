"""
Custom exceptions for storage operations.

This module defines a hierarchy of exceptions for handling storage-related errors,
including configuration errors, connection errors, and operation-specific errors.
"""


class StorageError(Exception):
    """Base exception for all storage operations."""

    def __init__(
        self,
        message: str,
        file_key: str | None = None,
        cause: Exception | None = None,
    ):
        self.message = message
        self.file_key = file_key
        self.cause = cause
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.file_key:
            parts.append(f"file_key={self.file_key}")
        if self.cause:
            parts.append(f"cause={self.cause}")
        return ", ".join(parts)


class StorageConfigError(StorageError):
    """Exception raised when storage configuration is invalid or missing."""

    pass


class StorageConnectionError(StorageError):
    """Exception raised when connection to storage backend fails."""

    pass


class StorageUploadError(StorageError):
    """Exception raised when file upload operation fails."""

    pass


class StorageDownloadError(StorageError):
    """Exception raised when file download operation fails."""

    pass


class StorageDeleteError(StorageError):
    """Exception raised when file delete operation fails."""

    pass
