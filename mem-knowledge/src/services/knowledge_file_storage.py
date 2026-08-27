"""Knowledge-file adapter over the shared storage backend."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from ..infrastructure.storage import StorageManager


def generate_kb_file_key(
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
    file_ext: str,
) -> str:
    if file_ext and not file_ext.startswith("."):
        file_ext = f".{file_ext}"
    return f"kb/{kb_id}/{file_id}{file_ext}"


class KnowledgeFileStorage:
    def __init__(self, manager: StorageManager):
        self._manager = manager

    async def upload(
        self,
        file_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        return await (await self._manager.backend()).upload(
            file_key,
            content,
            content_type,
        )

    async def download(self, file_key: str) -> bytes:
        return await (await self._manager.backend()).download(file_key)

    async def download_stream(
        self,
        file_key: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        backend = await self._manager.backend()
        async for chunk in backend.download_stream(file_key, chunk_size):
            yield chunk

    async def delete(self, file_key: str) -> bool:
        return await (await self._manager.backend()).delete(file_key)
