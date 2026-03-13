"""
AWS S3 storage backend implementation.

This module provides a storage backend that stores files on AWS S3
using the boto3 SDK.
"""

import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError

from app.core.storage.base import StorageBackend
from app.core.storage_exceptions import (
    StorageConfigError,
    StorageConnectionError,
    StorageDeleteError,
    StorageDownloadError,
    StorageUploadError,
)

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    """
    AWS S3 storage implementation.

    This class implements the StorageBackend interface for storing files
    on AWS Simple Storage Service (S3).

    Attributes:
        client: The boto3 S3 client instance.
        bucket_name: The name of the S3 bucket.
        region: The AWS region.
    """
    AMAZON_S3_ENDPOINT_MAP = {
        "us-east-1": "https://s3.us-east-1.amazonaws.com",  # 特殊：无地域后缀
        "us-east-2": "https://s3.us-east-2.amazonaws.com",
        "us-west-1": "https://s3.us-west-1.amazonaws.com",
        "us-west-2": "https://s3.us-west-2.amazonaws.com",
        "ap-east-1": "https://s3.ap-east-1.amazonaws.com",  # 香港
        "ap-southeast-1": "https://s3.ap-southeast-1.amazonaws.com",  # 新加坡
        "ap-southeast-2": "https://s3.ap-southeast-2.amazonaws.com",  # 悉尼
        "ap-northeast-1": "https://s3.ap-northeast-1.amazonaws.com",  # 东京
        "eu-central-1": "https://s3.eu-central-1.amazonaws.com",  # 法兰克福
        "eu-west-1": "https://s3.eu-west-1.amazonaws.com",  # 爱尔兰
        # 可根据需要扩展其他地域
    }

    def __init__(
        self,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        endpoint_url: Optional[str] = None
    ):
        """
        Initialize the S3Storage backend.

        Args:
            region: The AWS region (e.g., 'us-east-1').
            access_key_id: The AWS access key ID.
            secret_access_key: The AWS secret access key.
            bucket_name: The name of the S3 bucket.
            endpoint_url: The complete URL to use for the constructed client.

        Raises:
            StorageConfigError: If any required configuration is missing.
            StorageConnectionError: If connection to S3 fails.
        """
        # Validate required configuration
        if not region:
            raise StorageConfigError(message="S3 region is required")
        if not access_key_id:
            raise StorageConfigError(message="S3 access_key_id is required")
        if not secret_access_key:
            raise StorageConfigError(message="S3 secret_access_key is required")
        if not bucket_name:
            raise StorageConfigError(message="S3 bucket_name is required")

        self.region = region
        self.bucket_name = bucket_name

        if not endpoint_url:
            # 优先匹配内置映射表（解决特殊地域）
            if region in self.AMAZON_S3_ENDPOINT_MAP:
                endpoint_url = self.AMAZON_S3_ENDPOINT_MAP[region]
            # 兜底：通用拼接（适配未配置的新地域）
            else:
                endpoint_url = f"https://s3.{region}.amazonaws.com"

        try:
            self.client = boto3.client(
                "s3",
                region_name=region,
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            )
            logger.info(
                f"S3Storage initialized with region: {region}, bucket: {bucket_name}"
            )
        except NoCredentialsError as e:
            logger.error(f"Invalid AWS credentials: {e}")
            raise StorageConfigError(
                message=f"Invalid AWS credentials: {e}",
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            raise StorageConnectionError(
                message=f"Failed to initialize S3 client: {e}",
                cause=e,
            )

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to S3.

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
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type

            self.client.put_object(
                Bucket=self.bucket_name,
                Key=file_key,
                Body=content,
                **extra_args,
            )
            logger.info(f"File uploaded to S3 successfully: {file_key}")
            return file_key

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"S3 ClientError uploading file {file_key}: {error_message}")
            raise StorageUploadError(
                message=f"Failed to upload file to S3 ({error_code}): {error_message}",
                file_key=file_key,
                cause=e,
            )
        except BotoCoreError as e:
            logger.error(f"S3 BotoCoreError uploading file {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file to S3: {e}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to upload file to S3 {file_key}: {e}")
            raise StorageUploadError(
                message=f"Failed to upload file to S3: {e}",
                file_key=file_key,
                cause=e,
            )

    async def download(self, file_key: str) -> bytes:
        """
        Download a file from S3.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            File content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
            StorageDownloadError: If the download operation fails.
        """
        try:
            response = self.client.get_object(
                Bucket=self.bucket_name,
                Key=file_key,
            )
            content = response["Body"].read()
            logger.info(f"File downloaded from S3 successfully: {file_key}")
            return content

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("NoSuchKey", "404"):
                logger.warning(f"File not found in S3: {file_key}")
                raise FileNotFoundError(f"File not found: {file_key}")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"S3 ClientError downloading file {file_key}: {error_message}")
            raise StorageDownloadError(
                message=f"Failed to download file from S3 ({error_code}): {error_message}",
                file_key=file_key,
                cause=e,
            )
        except BotoCoreError as e:
            logger.error(f"S3 BotoCoreError downloading file {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file from S3: {e}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to download file from S3 {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file from S3: {e}",
                file_key=file_key,
                cause=e,
            )

    async def delete(self, file_key: str) -> bool:
        """
        Delete a file from S3.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file was deleted successfully.

        Raises:
            StorageDeleteError: If the delete operation fails.
        """
        try:
            self.client.delete_object(
                Bucket=self.bucket_name,
                Key=file_key,
            )
            logger.info(f"File deleted from S3 successfully: {file_key}")
            return True

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"S3 ClientError deleting file {file_key}: {error_message}")
            raise StorageDeleteError(
                message=f"Failed to delete file from S3 ({error_code}): {error_message}",
                file_key=file_key,
                cause=e,
            )
        except BotoCoreError as e:
            logger.error(f"S3 BotoCoreError deleting file {file_key}: {e}")
            raise StorageDeleteError(
                message=f"Failed to delete file from S3: {e}",
                file_key=file_key,
                cause=e,
            )
        except Exception as e:
            logger.error(f"Failed to delete file from S3 {file_key}: {e}")
            raise StorageDeleteError(
                message=f"Failed to delete file from S3: {e}",
                file_key=file_key,
                cause=e,
            )

    async def exists(self, file_key: str) -> bool:
        """
        Check if a file exists in S3.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            True if the file exists, False otherwise.
        """
        try:
            self.client.head_object(
                Bucket=self.bucket_name,
                Key=file_key,
            )
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("404", "NoSuchKey"):
                return False
            logger.error(f"Failed to check file existence in S3 {file_key}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to check file existence in S3 {file_key}: {e}")
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
            url = self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": file_key,
                },
                ExpiresIn=expires,
            )
            logger.debug(f"Generated presigned URL for {file_key}, expires in {expires}s")
            return url
        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {file_key}: {e}")
            # Return a basic URL format as fallback
            return f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{file_key}"
