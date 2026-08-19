"""Local filesystem storage using asynchronous file operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
import aiofiles.os

from .config import LocalStorageConfig
from .errors import StorageDeleteError, StorageDownloadError, StorageUploadError
from .interface import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, config: LocalStorageConfig):
        self.config = config
        self.root_path = config.root_path
        self.root_path.mkdir(parents=True, exist_ok=True)

    def _path(self, file_key: str) -> Path:
        return self.root_path / file_key

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        path = self._path(file_key)
        try:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            async with aiofiles.open(path, "wb") as handle:
                await handle.write(content)
            return str(path)
        except Exception as exc:
            raise StorageUploadError(
                "Failed to upload local file",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def upload_stream(
        self,
        file_key: str,
        stream: AsyncIterator[bytes],
        content_type: str | None = None,
    ) -> int:
        path = self._path(file_key)
        try:
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            total = 0
            async with aiofiles.open(path, "wb") as handle:
                async for chunk in stream:
                    if not chunk:
                        continue
                    await handle.write(chunk)
                    total += len(chunk)
            return total
        except Exception as exc:
            raise StorageUploadError(
                "Failed to upload local file stream",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def download(self, file_key: str) -> bytes:
        path = self._path(file_key)
        if not await asyncio.to_thread(path.exists):
            raise FileNotFoundError(f"File not found: {file_key}")
        try:
            async with aiofiles.open(path, "rb") as handle:
                return await handle.read()
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise StorageDownloadError(
                "Failed to download local file",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        path = self._path(file_key)
        if not await asyncio.to_thread(path.exists):
            raise FileNotFoundError(f"File not found: {file_key}")
        try:
            async with aiofiles.open(path, "rb") as handle:
                while chunk := await handle.read(chunk_size):
                    yield chunk
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise StorageDownloadError(
                "Failed to stream local file",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def delete(self, file_key: str) -> bool:
        path = self._path(file_key)
        if not await asyncio.to_thread(path.exists):
            return False
        try:
            await aiofiles.os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            raise StorageDeleteError(
                "Failed to delete local file",
                file_key=file_key,
                cause=exc,
            ) from exc

    async def exists(self, file_key: str) -> bool:
        return await asyncio.to_thread(self._path(file_key).exists)

    async def get_signed_url(
        self,
        file_key: str,
        expires: int = 3600,
        file_name: str | None = None,
    ) -> None:
        return None
