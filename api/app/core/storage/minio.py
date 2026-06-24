"""
MinIO storage backend implementation.

MinIO is a self-hosted, S3-compatible object storage service. This backend
reuses boto3 against a MinIO endpoint (inheriting upload/download/delete/
exists/presigned-URL behavior from S3Storage) and only customizes:

- MinIO-specific configuration (endpoint_url 必填、region 可空)
- bucket 不存在时自动创建（便于本地/自建 MinIO 开箱即用）
- permanent/public URL 使用 MinIO 的 path-style 地址：
    {endpoint_url}/{bucket_name}/{file_key}
"""

import logging

from botocore.exceptions import ClientError

from app.core.storage.s3 import S3Storage
from app.core.storage_exceptions import StorageConfigError

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
            region: The region label (MinIO 不强制，为空时默认 us-east-1).
            access_key_id: The MinIO access key ID.
            secret_access_key: The MinIO secret access key.
            bucket_name: The name of the MinIO bucket.
            endpoint_url: The MinIO endpoint URL (e.g. http://127.0.0.1:9000).

        Raises:
            StorageConfigError: If endpoint_url or other required config is missing.
        """
        if not endpoint_url:
            raise StorageConfigError(message="MinIO endpoint_url is required")

        # MinIO 不强制 region；为空时给一个安全默认值，避免 AWS 专有的
        # LocationConstraint 问题，也满足 S3Storage 的非空校验。
        region = region or "us-east-1"

        # 需要在 super().__init__ 之前赋值，供后续 bucket 创建与永久 URL 生成使用
        self.endpoint_url = endpoint_url

        super().__init__(
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )

        # MinIO bucket 若不存在则自动创建（仅对自建/本地 MinIO 有意义）
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self) -> None:
        """检查 bucket 是否存在，不存在则自动创建。"""
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"MinIO bucket '{self.bucket_name}' already exists")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                try:
                    if self.region == "us-east-1":
                        # us-east-1 创建 bucket 不能带 LocationConstraint
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

        返回 MinIO 的 path-style 地址：
            {endpoint_url}/{bucket_name}/{file_key}

        Args:
            file_key: Unique identifier for the file in the storage system.

        Returns:
            A permanent URL for the file.
        """
        return f"{self.endpoint_url}/{self.bucket_name}/{file_key}"
