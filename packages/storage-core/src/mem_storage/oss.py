"""Aliyun OSS asynchronous storage adapter."""

from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import oss2
from oss2.exceptions import NoSuchKey, OssError

from .config import OSSStorageConfig
from .errors import (
    StorageConnectionError,
    StorageDeleteError,
    StorageDownloadError,
    StorageUploadError,
)
from .interface import StorageBackend


class OSSStorage(StorageBackend):
    def __init__(self, config: OSSStorageConfig, *, bucket: Any | None = None):
        self.config = config
        self.endpoint = config.endpoint
        self.bucket_name = config.bucket_name
        self.part_size = config.multipart_part_size
        if bucket is not None:
            self.bucket = bucket
            return
        try:
            auth = oss2.Auth(
                config.access_key_id.get_secret_value(),
                config.access_key_secret.get_secret_value(),
            )
            self.bucket = oss2.Bucket(
                auth,
                config.endpoint,
                config.bucket_name,
                connect_timeout=config.connect_timeout_s,
            )
        except Exception as exc:
            raise StorageConnectionError(
                "Failed to initialize OSS client",
                cause=exc,
            ) from exc

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        headers = {"Content-Type": content_type} if content_type else None
        try:
            await asyncio.to_thread(
                self.bucket.put_object,
                file_key,
                content,
                headers=headers,
            )
            return file_key
        except Exception as exc:
            raise StorageUploadError(
                "Failed to upload file to OSS",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def upload_stream(
        self,
        file_key: str,
        stream: AsyncIterator[bytes],
        content_type: str | None = None,
    ) -> int:
        headers = {"Content-Type": content_type} if content_type else None
        upload_id: str | None = None
        buffer = bytearray()
        total = 0
        parts: list[Any] = []
        try:
            initiated = await asyncio.to_thread(
                self.bucket.init_multipart_upload,
                file_key,
                headers=headers,
            )
            upload_id = initiated.upload_id
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
                        self.bucket.upload_part,
                        file_key,
                        upload_id,
                        part_number,
                        body,
                    )
                    parts.append(oss2.models.PartInfo(part_number, result.etag))
                    part_number += 1
            if buffer:
                result = await asyncio.to_thread(
                    self.bucket.upload_part,
                    file_key,
                    upload_id,
                    part_number,
                    bytes(buffer),
                )
                parts.append(oss2.models.PartInfo(part_number, result.etag))
            if not parts:
                await asyncio.to_thread(
                    self.bucket.abort_multipart_upload,
                    file_key,
                    upload_id,
                )
                upload_id = None
                await self.upload(file_key, b"", content_type)
                return 0
            await asyncio.to_thread(
                self.bucket.complete_multipart_upload,
                file_key,
                upload_id,
                parts,
            )
            upload_id = None
            return total
        except Exception as exc:
            if upload_id is not None:
                with suppress(OssError):
                    await asyncio.to_thread(
                        self.bucket.abort_multipart_upload,
                        file_key,
                        upload_id,
                    )
            raise StorageUploadError(
                "Failed to stream file to OSS",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def _get_object(self, file_key: str):
        try:
            return await asyncio.to_thread(self.bucket.get_object, file_key)
        except NoSuchKey as exc:
            raise FileNotFoundError(f"File not found: {file_key}") from exc
        except OssError as exc:
            raise StorageDownloadError(
                "Failed to download file from OSS",
                file_key=file_key,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageDownloadError(
                "Failed to download file from OSS",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def download(self, file_key: str) -> bytes:
        result = await self._get_object(file_key)
        try:
            return await asyncio.to_thread(result.read)
        except Exception as exc:
            raise StorageDownloadError(
                "Failed to read file body from OSS",
                file_key=file_key,
                cause=exc,
            ) from exc
        finally:
            close = getattr(result, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    async def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        result = await self._get_object(file_key)
        try:
            if hasattr(result, "iter_chunks"):
                iterator = result.iter_chunks(chunk_size=chunk_size)
                while True:
                    chunk = await asyncio.to_thread(next, iterator, None)
                    if chunk is None:
                        break
                    if chunk:
                        yield chunk
            else:
                while chunk := await asyncio.to_thread(result.read, chunk_size):
                    yield chunk
        except Exception as exc:
            raise StorageDownloadError(
                "Failed to read file stream from OSS",
                file_key=file_key,
                cause=exc,
            ) from exc
        finally:
            close = getattr(result, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    async def delete(self, file_key: str) -> bool:
        try:
            await asyncio.to_thread(self.bucket.delete_object, file_key)
            return True
        except Exception as exc:
            raise StorageDeleteError(
                "Failed to delete file from OSS",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def exists(self, file_key: str) -> bool:
        try:
            return bool(await asyncio.to_thread(self.bucket.object_exists, file_key))
        except OssError:
            return False

    async def get_signed_url(
        self,
        file_key: str,
        expires: int = 3600,
        file_name: str | None = None,
    ) -> str:
        params: dict[str, str] = {}
        if file_name:
            encoded = urllib.parse.quote(file_name.encode("utf-8"))
            params["response-content-disposition"] = (
                f"attachment; filename*=UTF-8''{encoded}"
            )
        try:
            return await asyncio.to_thread(
                self.bucket.sign_url,
                "GET",
                file_key,
                expires,
                params=params or None,
            )
        except OssError:
            host = self.endpoint.removeprefix("https://").removeprefix("http://")
            return f"https://{self.bucket_name}.{host}/{file_key}"
