from __future__ import annotations

from collections.abc import Mapping
from typing import Self

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryRelationshipType
from app.core.memory.storage.models import (
    GraphWriteResult,
    MemoryGraphWriteCommand,
    NodeFilter,
    NodeProjection,
    NodeSort,
    RelationshipFilter,
    RelationshipPattern,
    RelationshipProjection,
    RelationshipSort,
    StorageReadResult,
    StorageWriteResult,
)
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.router.read_router import ReadRouter
from app.core.memory.storage.router.write_router import WriteRouter

memory_storage_service: "MemoryStorageService | None" = None


class MemoryStorageService:
    def __init__(self, backend_factory: BackendFactory) -> None:
        self._backend_factory = backend_factory
        self._read_router = ReadRouter(backend_factory)
        self._write_router = WriteRouter(backend_factory)

    @classmethod
    async def create(cls) -> Self:
        """Create the service and all storage clients during app lifespan."""
        return cls(await BackendFactory.create())

    async def search_by_embedding(
            self,
            node_filters: Mapping[MemoryNodeLabel, NodeFilter],
            embed: list,
            pre_limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        return await self._read_router.search_by_embedding(
            node_filters,
            embed,
            pre_limit,
            projection,
        )

    async def search_by_fulltext(
            self,
            node_filters: Mapping[MemoryNodeLabel, NodeFilter],
            text: str,
            pre_limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        return await self._read_router.search_by_fulltext(
            node_filters,
            text,
            pre_limit,
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

    async def search_relationships_by_graph(
            self,
            pattern: RelationshipPattern,
            rel_filter: RelationshipFilter,
            projection: RelationshipProjection | None = None,
            sort: RelationshipSort | None = None,
    ) -> StorageReadResult:
        return await self._read_router.search_relationships_by_graph(
            pattern,
            rel_filter,
            projection,
            sort,
        )

    async def save_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
    ) -> StorageWriteResult:
        return await self._write_router.save_node(label, data)

    async def save_memory_graph(
            self,
            command: MemoryGraphWriteCommand,
    ) -> GraphWriteResult:
        return await self._write_router.save_memory_graph(command)

    async def save_memory_summaries(self, summaries) -> GraphWriteResult:
        return await self._write_router.save_memory_summaries(summaries)

    async def update_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
            node_filter: NodeFilter,
    ) -> StorageWriteResult:
        return await self._write_router.update_node(label, data, node_filter)

    async def delete_node(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            draft: bool = False,
    ) -> StorageWriteResult:
        return await self._write_router.delete_node(label, node_filter, draft)

    async def save_relationship(
            self,
            relationship_type: MemoryRelationshipType,
            source: str,
            target: str,
            data: dict,
    ) -> StorageWriteResult:
        return await self._write_router.save_relationship(
            relationship_type,
            source,
            target,
            data,
        )

    async def update_relationship(
            self,
            relationship_type: MemoryRelationshipType,
            data: dict,
            rel_filter: RelationshipFilter,
    ) -> StorageWriteResult:
        return await self._write_router.update_relationship(
            relationship_type,
            data,
            rel_filter,
        )

    async def delete_relationship(
            self,
            relationship_type: MemoryRelationshipType,
            rel_filter: RelationshipFilter,
    ) -> StorageWriteResult:
        return await self._write_router.delete_relationship(
            relationship_type,
            rel_filter,
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
