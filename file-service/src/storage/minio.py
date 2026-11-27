"""
MinIO storage backend implementation.

MinIO is a self-hosted, S3-compatible object storage service. This backend
reuses boto3 against a MinIO endpoint (inheriting upload/download/delete/
exists/presigned-URL behavior from S3Storage) and only customizes:

- MinIO-specific configuration (endpoint_url required, region optional)
- Automatic bucket creation when missing (so local/self-hosted MinIO works out of the box)
- Permanent/public URLs use MinIO path-style addressing:
    {endpoint_url}/{bucket_name}/{file_key}
"""

import asyncio
import logging
from typing import AsyncIterator

from botocore.exceptions import BotoCoreError, ClientError

from .s3 import S3Storage
from .exceptions import StorageConfigError, StorageDownloadError

logger = logging.getLogger(__name__)


class MinIOStorage(S3Storage):
    """
    MinIO (S3-compatible) storage backend.

    Inherits all object operations from S3Storage and only overrides
    initialization (MinIO endpoint + bucket auto-creation) and permanent
    URL generation (MinIO path-style addressing).

    Attributes:
        endpoint_url: The MinIO endpoint URL (e.g. http://127.0.0.1:9000).
    """

    def __init__(
        self,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        endpoint_url: str,
    ):
        """
        Initialize the MinIOStorage backend.

        Args:
            region: The region label (optional for MinIO; defaults to us-east-1 when empty).
            access_key_id: The MinIO access key ID.
            secret_access_key: The MinIO secret access key.
            bucket_name: The name of the MinIO bucket.
            endpoint_url: The MinIO endpoint URL (e.g. http://127.0.0.1:9000).

        Raises:
            StorageConfigError: If endpoint_url or other required config is missing.
        """
        if not endpoint_url:
            raise StorageConfigError(message="MinIO endpoint_url is required")

        # MinIO does not require a region; use a safe default when empty to avoid
        # the AWS-specific LocationConstraint issue and satisfy S3Storage's non-empty check.
        region = region or "us-east-1"

        # Must be assigned before super().__init__; used later for bucket creation
        # and permanent URL generation
        self.endpoint_url = endpoint_url

        super().__init__(
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )

        # Auto-create the bucket if missing (only meaningful for self-hosted/local MinIO)
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self) -> None:
        """Check whether the bucket exists; create it automatically when missing."""
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"MinIO bucket '{self.bucket_name}' already exists")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                try:
                    if self.region == "us-east-1":
                        # Creating a bucket in us-east-1 must not include LocationConstraint
                        self.client.create_bucket(Bucket=self.bucket_name)
                    else:
                        self.client.create_bucket(
                            Bucket=self.bucket_name,
                            CreateBucketConfiguration={"LocationConstraint": self.region},
                        )
                    logger.info(f"MinIO bucket '{self.bucket_name}' created automatically")
                except ClientError as create_err:
                    logger.warning(
                        f"Failed to create MinIO bucket '{self.bucket_name}': {create_err}. "
                        f"Upload will fail if bucket does not exist."
                    )
            else:
                logger.warning(
                    f"Failed to check MinIO bucket '{self.bucket_name}' existence: {e}. "
                    f"Proceeding anyway."
                )

    async def get_permanent_url(self, file_key: str) -> str:
        """
        Get a permanent public URL for the file (requires bucket public read).

        Returns the MinIO path-style address:
            {endpoint_url}/{bucket_name}/{file_key}

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            A permanent URL for the file.
        """
        return f"{self.endpoint_url}/{self.bucket_name}/{file_key}"

    async def download_stream(self, file_key: str) -> AsyncIterator[bytes]:
        """
        Stream a file from MinIO in chunks (suitable for StreamingResponse).

        Only MinIO needs this method: MinIO is typically deployed on an intranet, so
        its presigned URLs point at intranet endpoints (e.g. http://10.x.x.x:9000)
        that browsers cannot reach and that lack TLS; following a 302 redirect would
        trigger ERR_SSL_PROTOCOL_ERROR. MinIO file downloads therefore stream back
        through the backend as a proxy, and the browser only talks to this service.
        S3/OSS presigned URLs are public HTTPS and usable via redirect, so they do
        not need this method.

        Sync boto3 calls are offloaded via asyncio.to_thread to avoid blocking the event loop.

        Args:
            file_key: Unique identifier for the file in the storage system.

        Yields:
            Bytes chunks of the file content.

        Raises:
            FileNotFoundError: If the file does not exist.
            StorageDownloadError: If the download operation fails.
        """
        try:
            response = await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket_name,
                Key=file_key,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("NoSuchKey", "404"):
                logger.warning(f"File not found in MinIO: {file_key}")
                raise FileNotFoundError(f"File not found: {file_key}")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"MinIO ClientError opening stream for {file_key}: {error_message}")
            raise StorageDownloadError(
                message=f"Failed to download file from MinIO ({error_code}): {error_message}",
                file_key=file_key,
                cause=e,
            )
        except BotoCoreError as e:
            logger.error(f"MinIO BotoCoreError opening stream for {file_key}: {e}")
            raise StorageDownloadError(
                message=f"Failed to download file from MinIO: {e}",
                file_key=file_key,
                cause=e,
            )

        body = response["Body"]
        chunk_iter = body.iter_chunks(chunk_size=64 * 1024)
        try:
            while True:
                chunk = await asyncio.to_thread(next, chunk_iter, None)
                if not chunk:
                    break
                yield chunk
        finally:
            # Release the underlying connection (especially important when the client disconnects early)
            await asyncio.to_thread(body.close)
