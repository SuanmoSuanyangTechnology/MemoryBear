"""MinIO adapter built on the S3-compatible implementation."""

from __future__ import annotations

import asyncio
from typing import Any

from botocore.exceptions import ClientError

from .config import MinIOStorageConfig, S3StorageConfig
from .errors import StorageConnectionError
from .s3 import S3Storage


class MinIOStorage(S3Storage):
    def __init__(
        self,
        config: MinIOStorageConfig,
        *,
        client: Any | None = None,
        owns_client: bool | None = None,
    ):
        self.minio_config = config
        self._bucket_ready = not config.ensure_bucket
        self._bucket_lock = asyncio.Lock()
        super().__init__(
            S3StorageConfig(
                region=config.region or "us-east-1",
                access_key_id=config.access_key_id,
                secret_access_key=config.secret_access_key,
                bucket_name=config.bucket_name,
                endpoint_url=config.endpoint_url,
                multipart_part_size=config.multipart_part_size,
            ),
            client=client,
            owns_client=owns_client,
        )

    async def _ensure_ready(self) -> None:
        if self._bucket_ready:
            return
        async with self._bucket_lock:
            if self._bucket_ready:
                return
            try:
                await asyncio.to_thread(
                    self.client.head_bucket,
                    Bucket=self.bucket_name,
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code not in {"404", "NoSuchBucket"}:
                    raise StorageConnectionError(
                        "Failed to inspect MinIO bucket",
                        cause=exc,
                    ) from exc
                params: dict[str, Any] = {"Bucket": self.bucket_name}
                if self.region != "us-east-1":
                    params["CreateBucketConfiguration"] = {
                        "LocationConstraint": self.region
                    }
                await asyncio.to_thread(self.client.create_bucket, **params)
            self._bucket_ready = True
