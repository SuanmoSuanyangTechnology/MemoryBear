"""Aliyun OSS asynchronous storage adapter."""

from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import AsyncIterator
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
from .requests import StorageOperation, StorageRequests


class OSSStorage(StorageBackend):
    def __init__(
        self,
        config: OSSStorageConfig,
        *,
        bucket: Any | None = None,
        owns_bucket: bool | None = None,
    ):
        self.config = config
        self.endpoint = config.endpoint
        self.bucket_name = config.bucket_name
        self.part_size = config.multipart_part_size
        self._owns_bucket = bucket is None if owns_bucket is None else owns_bucket
        self._closed = False
        self._requests = StorageRequests(config.max_concurrency)
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
                session=oss2.Session(pool_size=config.connection_pool_size),
            )
        except Exception as exc:
            raise StorageConnectionError(
                "Failed to initialize OSS client",
                cause=exc,
            ) from exc

    @staticmethod
    def _request(function, *args, **kwargs):
        result = function(*args, **kwargs)
        response = getattr(result, "resp", None)
        if response is not None:
            try:
                response.read()
            finally:
                response.response.close()
        return result

    async def ping(self) -> None:
        await self._requests.call(self._request, self.bucket.get_bucket_info)

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        headers = {"Content-Type": content_type} if content_type else None
        try:
            await self._requests.call(
                self._request,
                self.bucket.put_object,
                file_key,
                content,
                headers=headers,
                storage_key=file_key,
            )
            return file_key
        except OssError as exc:
            raise StorageUploadError(
                f"Failed to upload file to OSS: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageUploadError(
                f"Failed to upload file to OSS: {exc}",
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

        def start_upload() -> None:
            nonlocal upload_id
            upload_id = self._request(
                self.bucket.init_multipart_upload,
                file_key,
                headers=headers,
            ).upload_id

        def complete_upload() -> None:
            nonlocal upload_id
            self._request(self.bucket.complete_multipart_upload, file_key, upload_id, parts)
            upload_id = None

        def abort_upload() -> None:
            nonlocal upload_id
            if upload_id is not None:
                self._request(self.bucket.abort_multipart_upload, file_key, upload_id)
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
                            self._request,
                            self.bucket.upload_part,
                            file_key,
                            upload_id,
                            part_number,
                            body,
                        )
                        parts.append(oss2.models.PartInfo(part_number, result.etag))
                        part_number += 1
                if buffer:
                    result = await operation.call(
                        self._request,
                        self.bucket.upload_part,
                        file_key,
                        upload_id,
                        part_number,
                        bytes(buffer),
                    )
                    parts.append(oss2.models.PartInfo(part_number, result.etag))
                if not parts:
                    await operation.call(abort_upload)
                    await operation.call(
                        self._request, self.bucket.put_object, file_key, b"", headers=headers
                    )
                    return 0
                await operation.call(complete_upload)
                return total
            except Exception as exc:
                raise StorageUploadError(
                    f"Failed to stream upload file to OSS: {exc}",
                    file_key=file_key,
                    cause=exc,
                ) from exc

    def _open_download(self, operation: StorageOperation, file_key: str):
        try:
            result = self.bucket.get_object(file_key)
            operation.cleanup = result.close
            return result
        except NoSuchKey as exc:
            raise FileNotFoundError(f"File not found: {file_key}") from exc
        except Exception as exc:
            raise StorageDownloadError(
                f"Failed to download file from OSS: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def download(self, file_key: str) -> bytes:
        async with self._requests.operation(file_key) as operation:
            body = await operation.call(self._open_download, operation, file_key)
            try:
                return await operation.call(body.read)
            except Exception as exc:
                raise StorageDownloadError(
                    f"Failed to download file from OSS: {exc}",
                    file_key=file_key,
                    cause=exc,
                ) from exc

    async def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        async with self._requests.operation(file_key) as operation:
            body = await operation.call(self._open_download, operation, file_key)
            try:
                while chunk := await operation.call(body.read, chunk_size):
                    yield chunk
            except Exception as exc:
                raise StorageDownloadError(
                    f"Failed to download file from OSS: {exc}",
                    file_key=file_key,
                    cause=exc,
                ) from exc

    async def delete(self, file_key: str) -> bool:
        try:
            await self._requests.call(
                self._request,
                self.bucket.delete_object,
                file_key,
                storage_key=file_key,
            )
            return True
        except OssError as exc:
            raise StorageDeleteError(
                f"Failed to delete file from OSS: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageDeleteError(
                f"Failed to delete file from OSS: {exc}",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def exists(self, file_key: str) -> bool:
        try:
            await self._requests.call(
                self._request,
                self.bucket.get_object_meta,
                file_key,
                storage_key=file_key,
            )
            return True
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
            params["response-content-disposition"] = f"attachment; filename*=UTF-8''{encoded}"
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

    def _close_target(self):
        close = getattr(self.bucket, "close", None)
        if callable(close):
            return close
        session = getattr(self.bucket, "session", None)
        close = getattr(session, "close", None)
        if callable(close):
            return close
        return getattr(getattr(session, "session", None), "close", None)

    def close(self) -> None:
        if self._closed:
            return
        self._requests.drain()
        if self._owns_bucket:
            close = self._close_target()
            if close is not None:
                close()
        self._closed = True

    async def aclose(self) -> None:
        if self._closed:
            return
        await self._requests.aclose()
        if self._owns_bucket:
            aclose = getattr(self.bucket, "aclose", None)
            if callable(aclose):
                await aclose()
            else:
                close = self._close_target()
                if close is not None:
                    await asyncio.to_thread(close)
        self._closed = True
