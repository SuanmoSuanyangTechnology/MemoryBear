from __future__ import annotations

from typing import Self

from app.core.memory.storage.enums import MemoryNodeLabel
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSort,
    StorageReadResult,
)
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.router.read_router import ReadRouter


memory_storage_service: "MemoryStorageService | None" = None


class MemoryStorageService:
    def __init__(self, backend_factory: BackendFactory) -> None:
        self._backend_factory = backend_factory
        self._read_router = ReadRouter(backend_factory)

    @classmethod
    async def create(cls) -> Self:
        """Create the service and all storage clients during app lifespan."""
        return cls(await BackendFactory.create())

    async def search_by_embedding(
        self,
        labels: list[MemoryNodeLabel],
        node_filter: NodeFilter,
        embed: list,
        limit: int,
        projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        return await self._read_router.search_by_embedding(
            labels,
            node_filter,
            embed,
            limit,
            projection,
        )

    async def search_by_fulltext(
        self,
        labels: list[MemoryNodeLabel],
        node_filter: NodeFilter,
        text: str,
        limit: int,
        projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        return await self._read_router.search_by_fulltext(
            labels,
            node_filter,
            text,
            limit,
            projection,
        )

    async def get_node(
        self,
        label: MemoryNodeLabel,
        node_filter: NodeFilter,
        projection: NodeProjection | None = None,
        node_sort: NodeSort | None = None,
    ) -> StorageReadResult:
        """Read nodes through the read router."""
        return await self._read_router.get_node(
            label,
            node_filter,
            projection,
            node_sort,
        )

    async def close(self) -> None:
        await self._backend_factory.close()


async def initialize_storage_service() -> MemoryStorageService:
    """Initialize and return the process-wide storage service singleton."""
    global memory_storage_service
    if memory_storage_service is None:
        memory_storage_service = await MemoryStorageService.create()
    return memory_storage_service


def get_storage_service() -> MemoryStorageService:
    """Return the initialized process-wide storage service."""
    if memory_storage_service is None:
        raise RuntimeError(
            "MemoryStorageService is not initialized; initialize it in the API lifespan first"
        )
    return memory_storage_service


async def close_storage_service() -> None:
    """Close and clear the process-wide storage service singleton."""
    global memory_storage_service
    if memory_storage_service is not None:
        service = memory_storage_service
        memory_storage_service = None
        await service.close()
