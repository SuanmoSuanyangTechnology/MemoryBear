"""Backend-neutral asynchronous storage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class StorageBackend(ABC):
    @abstractmethod
    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def upload_stream(
        self,
        file_key: str,
        stream: AsyncIterator[bytes],
        content_type: str | None = None,
    ) -> int: ...

    @abstractmethod
    async def download(self, file_key: str) -> bytes: ...

    @abstractmethod
    def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]: ...

    @abstractmethod
    async def delete(self, file_key: str) -> bool: ...

    @abstractmethod
    async def exists(self, file_key: str) -> bool: ...

    @abstractmethod
    async def get_signed_url(
        self,
        file_key: str,
        expires: int = 3600,
        file_name: str | None = None,
    ) -> str | None: ...
