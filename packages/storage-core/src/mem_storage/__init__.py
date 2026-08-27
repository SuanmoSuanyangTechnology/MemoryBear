"""Backend-neutral storage contracts and adapters."""

from __future__ import annotations

from importlib import import_module

from .config import (
    LocalStorageConfig,
    MinIOStorageConfig,
    OSSStorageConfig,
    S3StorageConfig,
    StorageConfig,
    StorageType,
)
from .errors import (
    StorageConfigError,
    StorageConnectionError,
    StorageDeleteError,
    StorageDownloadError,
    StorageError,
    StorageUploadError,
)
from .factory import create_storage
from .interface import StorageBackend

_BACKEND_EXPORTS = {
    "LocalStorage": (".local", "LocalStorage"),
    "MinIOStorage": (".minio", "MinIOStorage"),
    "OSSStorage": (".oss", "OSSStorage"),
    "S3Storage": (".s3", "S3Storage"),
}

__all__ = [
    "LocalStorage",
    "LocalStorageConfig",
    "MinIOStorage",
    "MinIOStorageConfig",
    "OSSStorage",
    "OSSStorageConfig",
    "S3Storage",
    "S3StorageConfig",
    "StorageBackend",
    "StorageConfig",
    "StorageConfigError",
    "StorageConnectionError",
    "StorageDeleteError",
    "StorageDownloadError",
    "StorageError",
    "StorageType",
    "StorageUploadError",
    "create_storage",
]


def __getattr__(name: str):
    try:
        module_name, attribute_name = _BACKEND_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    return getattr(import_module(module_name, __name__), attribute_name)
