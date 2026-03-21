"""
Aliyun OSS storage backend implementation.

This module provides a storage backend that stores files on Aliyun Object
Storage Service (OSS) using the oss2 SDK.
"""

import logging
from typing import Optional

import oss2
from oss2.exceptions import NoSuchKey, OssError

from app.core.storage.base import StorageBackend
from app.core.storage_exceptions import (
    StorageConfigError,
    StorageConnectionError,
    StorageDeleteError,
    StorageDownloadError,
    StorageUploadError,
)

logger = logging.getLogger(__name__)


class OSSStorage(StorageBackend):
    """
    Aliyun OSS storage implementation.

    This class implements the StorageBackend interface for storing files
    on Aliyun Object Storage Service (OSS).

    Attributes:
        bucket: The oss2.Bucket instance for OSS operations.
        bucket_name: The name of the OSS bucket.
        endpoint: The OSS endpoint URL.
    """

    def __init__(
        self,
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
        bucket_name: str,
    ):
        """
        Initialize the OSSStorage backend.

        Args:
            endpoint: The OSS endpoint URL (e.g., 'https://oss-cn-hangzhou.aliyuncs.com').
            access_key_id: The Aliyun access key ID.
            access_key_secret: The Aliyun access key secret.
            bucket_name: The name of the OSS bucket.

        Raises:
            StorageConfigError: If any required configuration is missing.
        """
        # Validate required configuration
        if not endpoint:
            raise StorageConfigError(message="OSS endpoint is required")
        if not access_key_id:
            raise StorageConfigError(message="OSS access_key_id is required")
        if not access_key_secret:
            raise StorageConfigError(message="OSS access_key_secret is required")
        if not bucket_name:
            raise StorageConfigError(message="OSS bucket_name is required")

        self.endpoint = endpoint
        self.bucket_name = bucket_name

        try:
            auth = oss2.Auth(access_key_id, access_key_secret)
            self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
            logger.info(
                f"OSSStorage initialized with endpoint: {endpoint}, bucket: {bucket_name}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize OSS client: {e}")
            raise StorageConnectionError(
                message=f"Failed to initialize OSS client: {e}",
                cause=e,
            )

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to OSS.

        Args:
            file_key: Unique identifier for the file in the storage system.
            content: File content as bytes.
            content_type: Optional MIME type of the file.

        Returns:
            The file key of the uploaded file.

        Raises:
            StorageUploadError: If the upload operation fails.
        """
        try:
            headers = {}
            if content_type:
                headers["Content-Type"] = content_type

            self.bucket.put_object(file_key, content, headers=headers if headers else None)
            logger.info(f"File uploaded to OSS successfully: {file_key}")
            return file_key

        except OssError as e:
            logger.error(f"OSS error uploading file {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file to OSS: {e.message}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to upload file to OSS {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file to OSS: {e}",
                file_key=file_key,
                cause=e,
            )

    async def download(self, file_key: str) -> bytes:
        """
        Download a file from OSS.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            File content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
            StorageDownloadError: If the download operation fails.
        """
        try:
            result = self.bucket.get_object(file_key)
            content = result.read()
            logger.info(f"File downloaded from OSS successfully: {file_key}")
            return content

        except NoSuchKey:
            logger.warning(f"File not found in OSS: {file_key}")
            raise FileNotFoundError(f"File not found: {file_key}")
        except OssError as e:
            logger.error(f"OSS error downloading file {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file from OSS: {e.message}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to download file from OSS {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file from OSS: {e}",
                file_key=file_key,
                cause=e,
            )

    async def delete(self, file_key: str) -> bool:
        """
        Delete a file from OSS.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file was deleted successfully.

        Raises:
            StorageDeleteError: If the delete operation fails.
        """
        try:
            self.bucket.delete_object(file_key)
            logger.info(f"File deleted from OSS successfully: {file_key}")
            return True

        except OssError as e:
            logger.error(f"OSS error deleting file {file_key}: {e}")
            raise StorageDeleteError(
                message=f"Failed to delete file from OSS: {e.message}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to delete file from OSS {file_key}: {e}")
            raise StorageDeleteError(
                message=f"Failed to delete file from OSS: {e}",
                file_key=file_key,
                cause=e,
            )

    async def exists(self, file_key: str) -> bool:
        """
        Check if a file exists in OSS.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file exists, False otherwise.
        """
        try:
            return self.bucket.object_exists(file_key)
        except Exception as e:
            logger.error(f"Failed to check file existence in OSS {file_key}: {e}")
            return False

    async def get_url(self, file_key: str, expires: int = 3600) -> str:
        """
        Get a presigned URL for accessing the file.

        Args:
            file_key: Unique identifier for the file in the storage system.
            expires: URL validity period in seconds (default: 1 hour).

        Returns:
            A presigned URL for accessing the file.
        """
        try:
            url = self.bucket.sign_url("GET", file_key, expires)
            logger.debug(f"Generated presigned URL for {file_key}, expires in {expires}s")
            return url
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {file_key}: {e}")
            # Return a basic URL format as fallback
            return f"https://{self.bucket_name}.{self.endpoint.replace('https://', '').replace('http://', '')}/{file_key}"
