"""AWS S3-compatible asynchronous storage adapter."""

from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import AsyncIterator
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from .config import S3StorageConfig
from .errors import (
    StorageConfigError,
    StorageConnectionError,
    StorageDeleteError,
    StorageDownloadError,
    StorageUploadError,
)
from .interface import StorageBackend
from .requests import StorageOperation, StorageRequests


class S3Storage(StorageBackend):
    def __init__(
        self,
        config: S3StorageConfig,
        *,
        client: Any | None = None,
        owns_client: bool | None = None,
    ):
        self.config = config
        self.region = config.region
        self.bucket_name = config.bucket_name
        self.part_size = config.multipart_part_size
        self._owns_client = client is None if owns_client is None else owns_client
        self._closed = False
        self._requests = StorageRequests(config.max_concurrency)
        if client is not None:
            self.client = client
            return
        endpoint_url = config.endpoint_url or f"https://s3.{config.region}.amazonaws.com"
        try:
            self.client = boto3.client(
                "s3",
                region_name=config.region,
                endpoint_url=endpoint_url,
                aws_access_key_id=config.access_key_id.get_secret_value(),
                aws_secret_access_key=config.secret_access_key.get_secret_value(),
                config=Config(max_pool_connections=config.connection_pool_size),
            )
        except NoCredentialsError as exc:
            raise StorageConfigError("Invalid S3 credentials", cause=exc) from exc
        except Exception as exc:
            raise StorageConnectionError(
                "Failed to initialize S3 client",
                cause=exc,
            ) from exc

    async def ping(self) -> None:
        await self._requests.call(self.client.head_bucket, Bucket=self.bucket_name)

    async def _ensure_ready(self) -> None:
        return None

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        await self._ensure_ready()
        params: dict[str, Any] = {
            "Bucket": self.bucket_name,
            "Key": file_key,
            "Body": content,
        }
        if content_type:
            params["ContentType"] = content_type
        try:
            await self._requests.call(self.client.put_object, storage_key=file_key, **params)
            return file_key
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            message = exc.response.get("Error", {}).get("Message", str(exc))
            raise StorageUploadError(
                f"Failed to upload file to S3 ({code}): {message}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except BotoCoreError as exc:
            raise StorageUploadError(
                f"Failed to upload file to S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageUploadError(
                f"Failed to upload file to S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def upload_stream(
        self,
        file_key: str,
        stream: AsyncIterator[bytes],
        content_type: str | None = None,
    ) -> int:
        await self._ensure_ready()
        upload_id: str | None = None
        total = 0
        buffer = bytearray()
        parts: list[dict[str, Any]] = []

        def start_upload() -> None:
            nonlocal upload_id
            params = {"Bucket": self.bucket_name, "Key": file_key}
            if content_type:
                params["ContentType"] = content_type
            upload_id = self.client.create_multipart_upload(**params)["UploadId"]

        def complete_upload() -> None:
            nonlocal upload_id
            self.client.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=file_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            upload_id = None

        def abort_upload() -> None:
            nonlocal upload_id
            if upload_id is not None:
                self.client.abort_multipart_upload(
                    Bucket=self.bucket_name,
                    Key=file_key,
                    UploadId=upload_id,
                )
                upload_id = None

        async with self._requests.operation(file_key) as operation:
            operation.cleanup = abort_upload
            try:
                await operation.call(start_upload)
                part_number = 1
                async for chunk in stream:
                    if not chunk:
                        continue
                    buffer.extend(chunk)
                    total += len(chunk)
                    while len(buffer) >= self.part_size:
                        body = bytes(buffer[: self.part_size])
                        del buffer[: self.part_size]
                        result = await operation.call(
                            self.client.upload_part,
                            Bucket=self.bucket_name,
                            Key=file_key,
                            UploadId=upload_id,
                            PartNumber=part_number,
                            Body=body,
                        )
                        parts.append({"PartNumber": part_number, "ETag": result["ETag"]})
                        part_number += 1
                if buffer:
                    result = await operation.call(
                        self.client.upload_part,
                        Bucket=self.bucket_name,
                        Key=file_key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=bytes(buffer),
                    )
                    parts.append({"PartNumber": part_number, "ETag": result["ETag"]})
                if not parts:
                    await operation.call(abort_upload)
                    await operation.call(
                        self.client.put_object,
                        Bucket=self.bucket_name,
                        Key=file_key,
                        Body=b"",
                        **({"ContentType": content_type} if content_type else {}),
                    )
                    return 0
                await operation.call(complete_upload)
                return total
            except Exception as exc:
                raise StorageUploadError(
                    f"Failed to stream upload file to S3: {exc}",
                    file_key=file_key,
                    cause=exc,
                ) from exc

    def _open_download(self, operation: StorageOperation, file_key: str):
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=file_key)
            body = response["Body"]
            operation.cleanup = body.close
            return body
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") in {"NoSuchKey", "404"}:
                raise FileNotFoundError(f"File not found: {file_key}") from exc
            raise StorageDownloadError(
                f"Failed to download file from S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageDownloadError(
                f"Failed to download file from S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def download(self, file_key: str) -> bytes:
        await self._ensure_ready()
        async with self._requests.operation(file_key) as operation:
            body = await operation.call(self._open_download, operation, file_key)
            try:
                return await operation.call(body.read)
            except Exception as exc:
                raise StorageDownloadError(
                    f"Failed to download file from S3: {exc}",
                    file_key=file_key,
                    cause=exc,
                ) from exc

    async def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        await self._ensure_ready()
        async with self._requests.operation(file_key) as operation:
            body = await operation.call(self._open_download, operation, file_key)
            try:
                while chunk := await operation.call(body.read, chunk_size):
                    yield chunk
            except Exception as exc:
                raise StorageDownloadError(
                    f"Failed to download file from S3: {exc}",
                    file_key=file_key,
                    cause=exc,
                ) from exc

    async def delete(self, file_key: str) -> bool:
        await self._ensure_ready()
        try:
            await self._requests.call(
                self.client.delete_object,
                storage_key=file_key,
                Bucket=self.bucket_name,
                Key=file_key,
            )
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            message = exc.response.get("Error", {}).get("Message", str(exc))
            raise StorageDeleteError(
                f"Failed to delete file from S3 ({code}): {message}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except BotoCoreError as exc:
            raise StorageDeleteError(
                f"Failed to delete file from S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageDeleteError(
                f"Failed to delete file from S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def exists(self, file_key: str) -> bool:
        await self._ensure_ready()
        try:
            await self._requests.call(
                self.client.head_object,
                storage_key=file_key,
                Bucket=self.bucket_name,
                Key=file_key,
            )
            return True
        except ClientError:
            return False
        except BotoCoreError:
            return False

    async def get_signed_url(
        self,
        file_key: str,
        expires: int = 3600,
        file_name: str | None = None,
    ) -> str:
        await self._ensure_ready()
        params: dict[str, Any] = {"Bucket": self.bucket_name, "Key": file_key}
        if file_name:
            encoded = urllib.parse.quote(file_name.encode("utf-8"))
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{encoded}"
        try:
            return await asyncio.to_thread(
                self.client.generate_presigned_url,
                "get_object",
                Params=params,
                ExpiresIn=expires,
            )
        except (ClientError, BotoCoreError):
            return f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{file_key}"

    def close(self) -> None:
        if self._closed:
            return
        self._requests.drain()
        if self._owns_client:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        self._closed = True

    async def aclose(self) -> None:
        if self._closed:
            return
        await self._requests.aclose()
        if self._owns_client:
            close = getattr(self.client, "close", None)
            if callable(close):
                await asyncio.to_thread(close)
        self._closed = True
