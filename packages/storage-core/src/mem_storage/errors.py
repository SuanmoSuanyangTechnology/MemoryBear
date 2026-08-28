"""Storage error hierarchy independent from service HTTP semantics."""

from __future__ import annotations


class StorageError(Exception):
    def __init__(
        self,
        message: str,
        *,
        file_key: str | None = None,
        cause: Exception | None = None,
    ):
        self.message = message
        self.file_key = file_key
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause
        super().__init__(message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.file_key:
            parts.append(f"file_key={self.file_key}")
        if self.cause:
            parts.append(f"cause={self.cause}")
        return ", ".join(parts)


class StorageConfigError(StorageError):
    pass


class StorageConnectionError(StorageError):
    pass


class StorageUploadError(StorageError):
    pass


class StorageDownloadError(StorageError):
    pass


class StorageDeleteError(StorageError):
    pass
