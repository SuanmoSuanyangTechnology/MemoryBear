"""Create storage backends from explicit configuration."""

from __future__ import annotations

from .config import (
    LocalStorageConfig,
    MinIOStorageConfig,
    OSSStorageConfig,
    S3StorageConfig,
    StorageConfig,
)
from .interface import StorageBackend
from .local import LocalStorage
from .minio import MinIOStorage
from .oss import OSSStorage
from .s3 import S3Storage


def create_storage(config: StorageConfig) -> StorageBackend:
    if isinstance(config, LocalStorageConfig):
        return LocalStorage(config)
    if isinstance(config, OSSStorageConfig):
        return OSSStorage(config)
    if isinstance(config, MinIOStorageConfig):
        return MinIOStorage(config)
    if isinstance(config, S3StorageConfig):
        return S3Storage(config)
    raise TypeError(f"Unsupported storage config: {type(config).__name__}")
