"""
Aliyun OSS storage backend implementation.

This module provides a storage backend that stores files on Aliyun Object
Storage Service (OSS) using the oss2 SDK.
"""

import io
import logging
import urllib.parse
from typing import AsyncIterator, Optional

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
        connect_timeout: int = 30,
        multipart_threshold: int = 10 * 1024 * 1024,  # 10MB
    ):
        """
        Initialize the OSSStorage backend.

        Args:
            endpoint: The OSS endpoint URL (e.g., 'https://oss-cn-hangzhou.aliyuncs.com').
            access_key_id: The Aliyun access key ID.
            access_key_secret: The Aliyun access key secret.
            bucket_name: The name of the OSS bucket.
            connect_timeout: Connection timeout in seconds (default: 30).
            multipart_threshold: File size threshold for multipart upload (default: 10MB).

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
        self.multipart_threshold = multipart_threshold

        try:
            auth = oss2.Auth(access_key_id, access_key_secret)
            # 设置超时和重试
            self.bucket = oss2.Bucket(
                auth, 
                endpoint, 
                bucket_name,
                connect_timeout=connect_timeout
            )
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

            # 大文件使用分片上传
            if len(content) > self.multipart_threshold:
                logger.info(f"Using multipart upload for large file: {file_key} ({len(content)} bytes)")
                upload_id = self.bucket.init_multipart_upload(file_key, headers=headers if headers else None).upload_id
                parts = []
                part_size = 5 * 1024 * 1024  # 5MB per part
                part_num = 1
                
                for offset in range(0, len(content), part_size):
                    chunk = content[offset:offset + part_size]
                    result = self.bucket.upload_part(file_key, upload_id, part_num, chunk)
                    parts.append(oss2.models.PartInfo(part_num, result.etag))
                    part_num += 1
                
                self.bucket.complete_multipart_upload(file_key, upload_id, parts)
            else:
                self.bucket.put_object(file_key, content, headers=headers if headers else None)
            
            logger.info(f"File uploaded to OSS successfully: {file_key}")
            return file_key

        except OssError as e:
            logger.error(f"OSS error uploading file {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file to OSS: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to upload file to OSS {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file to OSS: {str(e)}",
                file_key=file_key,
                cause=e,
            )

    async def upload_stream(
        self,
        file_key: str,
        stream: AsyncIterator[bytes],
        content_type: Optional[str] = None,
    ) -> int:
        """Upload from async stream to OSS. Returns total bytes written."""
        buf = io.BytesIO()
        headers = {"Content-Type": content_type} if content_type else None
        upload_id = None
        
        try:
            # 收集流数据
            total_size = 0
            async for chunk in stream:
                if not chunk:
                    continue
                buf.write(chunk)
                total_size += len(chunk)
            
            content = buf.getvalue()
            
            if not content:
                raise StorageUploadError(
                    message="Empty stream content",
                    file_key=file_key,
                )
            
            # 大文件使用分片上传
            if len(content) > self.multipart_threshold:
                logger.info(f"Using multipart upload for stream: {file_key} ({len(content)} bytes)")
                upload_id = self.bucket.init_multipart_upload(file_key, headers=headers).upload_id
                parts = []
                part_size = 5 * 1024 * 1024  # 5MB
                part_num = 1
                
                for offset in range(0, len(content), part_size):
                    chunk = content[offset:offset + part_size]
                    result = self.bucket.upload_part(file_key, upload_id, part_num, chunk)
                    parts.append(oss2.models.PartInfo(part_num, result.etag))
                    part_num += 1
                
                self.bucket.complete_multipart_upload(file_key, upload_id, parts)
            else:
                self.bucket.put_object(file_key, content, headers=headers)
            
            logger.info(f"File stream uploaded to OSS successfully: {file_key} ({total_size} bytes)")
            return total_size
            
        except OssError as e:
            if upload_id:
                try:
                    self.bucket.abort_multipart_upload(file_key, upload_id)
                except:
                    pass
            logger.error(f"OSS error stream uploading file {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to stream upload file to OSS: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            if upload_id:
                try:
                    self.bucket.abort_multipart_upload(file_key, upload_id)
                except:
                    pass
            logger.error(f"Failed to stream upload file to OSS {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to stream upload file to OSS: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        finally:
            buf.close()

    async def download(self, file_key: str) -> bytes:
        """
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
                message=f"Failed to download file from OSS: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to download file from OSS {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file from OSS: {str(e)}",
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
                message=f"Failed to delete file from OSS: {str(e)}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to delete file from OSS {file_key}: {e}")
            raise StorageDeleteError(
                message=f"Failed to delete file from OSS: {str(e)}",
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

    async def get_url(
        self,
        file_key: str,
        expires: int = 3600,
        file_name: Optional[str] = None,
    ) -> str:
        """
        Get a presigned URL for accessing the file.

        Args:
            file_key: Unique identifier for the file in the storage system.
            expires: URL validity period in seconds (default: 1 hour).
            file_name: If set, adds Content-Disposition: attachment to force download.

        Returns:
            A presigned URL for accessing the file.
        """
        try:
            params = {}
            if file_name:
                filename_encoded = urllib.parse.quote(file_name.encode("utf-8"))
                params["response-content-disposition"] = f"attachment; filename*=UTF-8''{filename_encoded}"
            url = self.bucket.sign_url("GET", file_key, expires, params=params if params else None)
            logger.debug(f"Generated presigned URL for {file_key}, expires in {expires}s")
            return url
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {file_key}: {e}")
            return f"https://{self.bucket_name}.{self.endpoint.replace('https://', '').replace('http://', '')}/{file_key}"

    async def get_permanent_url(self, file_key: str) -> str:
        """
        Get a permanent public URL for the file (requires bucket public read).

        Returns:
            A permanent URL in the format: https://{bucket}.{endpoint}/{file_key}
        """
        host = self.endpoint.replace("https://", "").replace("http://", "")
        return f"https://{self.bucket_name}.{host}/{file_key}"
