"""Backend-neutral storage contracts and adapters."""

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
from .local import LocalStorage
from .minio import MinIOStorage
from .oss import OSSStorage
from .s3 import S3Storage

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
