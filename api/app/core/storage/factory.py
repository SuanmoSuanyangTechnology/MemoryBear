"""
Storage backend factory module.

This module provides a factory class for creating storage backend instances
based on configuration. It implements the singleton pattern to ensure only
one storage backend instance exists throughout the application lifecycle.
"""

import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.storage.base import StorageBackend
from app.core.storage_exceptions import StorageConfigError

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
        storage_type = settings.STORAGE_TYPE.lower()
        logger.info(f"Creating storage backend of type: {storage_type}")

        if storage_type == "local":
            from app.core.storage.local import LocalStorage

            base_path = Path("storage") / settings.FILE_PATH.lstrip("/")
            return LocalStorage(base_path=str(base_path))

        elif storage_type == "oss":
            from app.core.storage.oss import OSSStorage

            return OSSStorage(
                endpoint=settings.OSS_ENDPOINT,
                access_key_id=settings.OSS_ACCESS_KEY_ID,
                access_key_secret=settings.OSS_ACCESS_KEY_SECRET,
                bucket_name=settings.OSS_BUCKET_NAME,
            )

        elif storage_type == "s3":
            from app.core.storage.s3 import S3Storage

            return S3Storage(
                region=settings.S3_REGION,
                access_key_id=settings.S3_ACCESS_KEY_ID,
                secret_access_key=settings.S3_SECRET_ACCESS_KEY,
                bucket_name=settings.S3_BUCKET_NAME,
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
