"""File storage primitives used by migrated multimodal processing."""

from src.infrastructure.file_storage.base import StorageBackend
from src.infrastructure.file_storage.factory import StorageFactory
from src.infrastructure.file_storage.local import LocalStorage


class FileStorageService:
    @property
    def storage(self):
        return StorageFactory.get_storage()

    async def download_file(self, file_key):
        return await self.storage.download(file_key)


__all__ = ["StorageBackend", "LocalStorage", "StorageFactory", "FileStorageService"]
