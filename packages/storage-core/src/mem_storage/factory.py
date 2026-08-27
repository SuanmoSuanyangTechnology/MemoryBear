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


def create_storage(config: StorageConfig) -> StorageBackend:
    if isinstance(config, LocalStorageConfig):
        from .local import LocalStorage

        return LocalStorage(config)
    if isinstance(config, OSSStorageConfig):
        from .oss import OSSStorage

        return OSSStorage(config)
    if isinstance(config, MinIOStorageConfig):
        from .minio import MinIOStorage

        return MinIOStorage(config)
    if isinstance(config, S3StorageConfig):
        from .s3 import S3Storage

        return S3Storage(config)
    raise TypeError(f"Unsupported storage config: {type(config).__name__}")
