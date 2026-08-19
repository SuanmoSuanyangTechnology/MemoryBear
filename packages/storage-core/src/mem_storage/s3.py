"""AWS S3-compatible asynchronous storage adapter."""

from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import boto3
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
            )
        except NoCredentialsError as exc:
            raise StorageConfigError("Invalid S3 credentials", cause=exc) from exc
        except Exception as exc:
            raise StorageConnectionError(
                "Failed to initialize S3 client",
                cause=exc,
            ) from exc

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
            await asyncio.to_thread(self.client.put_object, **params)
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
        try:
            create_params: dict[str, Any] = {
                "Bucket": self.bucket_name,
                "Key": file_key,
            }
            if content_type:
                create_params["ContentType"] = content_type
            response = await asyncio.to_thread(
                self.client.create_multipart_upload,
                **create_params,
            )
            upload_id = response["UploadId"]
            part_number = 1
            async for chunk in stream:
                if not chunk:
                    continue
                buffer.extend(chunk)
                total += len(chunk)
                while len(buffer) >= self.part_size:
                    body = bytes(buffer[: self.part_size])
                    del buffer[: self.part_size]
                    result = await asyncio.to_thread(
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
                result = await asyncio.to_thread(
                    self.client.upload_part,
                    Bucket=self.bucket_name,
                    Key=file_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=bytes(buffer),
                )
                parts.append({"PartNumber": part_number, "ETag": result["ETag"]})
            if not parts:
                await asyncio.to_thread(
                    self.client.abort_multipart_upload,
                    Bucket=self.bucket_name,
                    Key=file_key,
                    UploadId=upload_id,
                )
                upload_id = None
                await self.upload(file_key, b"", content_type)
                return 0
            await asyncio.to_thread(
                self.client.complete_multipart_upload,
                Bucket=self.bucket_name,
                Key=file_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            upload_id = None
            return total
        except Exception as exc:
            if upload_id is not None:
                with suppress(ClientError, BotoCoreError):
                    await asyncio.to_thread(
                        self.client.abort_multipart_upload,
                        Bucket=self.bucket_name,
                        Key=file_key,
                        UploadId=upload_id,
                    )
            raise StorageUploadError(
                f"Failed to stream upload file to S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def _get_object(self, file_key: str):
        await self._ensure_ready()
        try:
            return await asyncio.to_thread(
                self.client.get_object,
                Bucket=self.bucket_name,
                Key=file_key,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404"}:
                raise FileNotFoundError(f"File not found: {file_key}") from exc
            raise StorageDownloadError(
                f"Failed to download file from S3 ({code}): "
                f"{exc.response.get('Error', {}).get('Message', str(exc))}",
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
        response = await self._get_object(file_key)
        body = response["Body"]
        try:
            return await asyncio.to_thread(body.read)
        except Exception as exc:
            raise StorageDownloadError(
                f"Failed to download file from S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    async def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        response = await self._get_object(file_key)
        body = response["Body"]
        iterator = body.iter_chunks(chunk_size=chunk_size)
        try:
            while True:
                chunk = await asyncio.to_thread(next, iterator, None)
                if chunk is None:
                    break
                if chunk:
                    yield chunk
        except Exception as exc:
            raise StorageDownloadError(
                f"Failed to download file from S3: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    async def delete(self, file_key: str) -> bool:
        await self._ensure_ready()
        try:
            await asyncio.to_thread(
                self.client.delete_object,
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
            await asyncio.to_thread(
                self.client.head_object,
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
            params["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{encoded}"
            )
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
        if self._owns_client:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        self._closed = True

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._owns_client:
            close = getattr(self.client, "close", None)
            if callable(close):
                await asyncio.to_thread(close)
        self._closed = True
