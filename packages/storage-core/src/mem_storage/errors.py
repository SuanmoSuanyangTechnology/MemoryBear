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
        if cause is not None:
            self.__cause__ = cause
        suffix = f"; file_key={file_key}" if file_key else ""
        super().__init__(f"{message}{suffix}")


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
