"""
Storage backend factory module.

This module provides a factory class for creating storage backend instances
based on configuration. It implements the singleton pattern to ensure only
one storage backend instance exists throughout the application lifecycle.
"""

import logging
from pathlib import Path
from typing import Optional

from ..config import current_settings
from .base import StorageBackend
from .exceptions import StorageConfigError

logger = logging.getLogger(__name__)


class StorageFactory:
    """
    Factory class for creating storage backend instances.

    This class implements the singleton pattern to ensure only one storage
    backend instance is created and reused throughout the application.

    Attributes:
        _instance: The singleton storage backend instance.
    """

    _instance: Optional[StorageBackend] = None

    @classmethod
    def get_storage(cls) -> StorageBackend:
        """
        Get the storage backend instance (singleton).

        Returns:
            The storage backend instance.

        Raises:
            ValueError: If the configured storage type is not supported.
            StorageConfigError: If required configuration is missing.
        """
        if cls._instance is None:
            cls._instance = cls._create_storage()
        return cls._instance

    @classmethod
    def _create_storage(cls) -> StorageBackend:
        """
        Create a storage backend instance based on configuration.

        Returns:
            A new storage backend instance.

        Raises:
            ValueError: If the configured storage type is not supported.
            StorageConfigError: If required configuration is missing.
        """
        storage_type = current_settings().storage_type.lower()
        logger.info(f"Creating storage backend of type: {storage_type}")

        if storage_type == "local":
            from .local import LocalStorage

            base_path = Path("storage") / current_settings().file_path.lstrip("/")
            return LocalStorage(base_path=str(base_path))

        elif storage_type == "oss":
            from .oss import OSSStorage

            return OSSStorage(
                endpoint=current_settings().oss_endpoint,
                access_key_id=current_settings().oss_access_key_id,
                access_key_secret=current_settings().oss_access_key_secret.get_secret_value(),
                bucket_name=current_settings().oss_bucket_name,
            )

        elif storage_type == "s3":
            from .s3 import S3Storage

            return S3Storage(
                region=current_settings().s3_region,
                access_key_id=current_settings().s3_access_key_id,
                secret_access_key=current_settings().s3_secret_access_key.get_secret_value(),
                bucket_name=current_settings().s3_bucket_name,
                endpoint_url=current_settings().s3_endpoint_url,
            )

        elif storage_type == "minio":
            from .minio import MinIOStorage

            # MinIO is S3-compatible; MinIOStorage handles endpoint,
            # bucket auto-creation and path-style permanent URLs.
            return MinIOStorage(
                region=current_settings().minio_region,
                access_key_id=current_settings().minio_access_key_id,
                secret_access_key=current_settings().minio_secret_access_key.get_secret_value(),
                bucket_name=current_settings().minio_bucket_name,
                endpoint_url=current_settings().minio_endpoint_url,
            )

        else:
            logger.error(f"Unsupported storage type: {storage_type}")
            raise ValueError(f"Unsupported storage type: {storage_type}")

    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance.

        This method is primarily used for testing purposes to allow
        creating new storage instances with different configurations.
        """
        cls._instance = None
        logger.debug("StorageFactory singleton instance reset")
