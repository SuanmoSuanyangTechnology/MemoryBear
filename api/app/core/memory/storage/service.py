from __future__ import annotations

from typing import Self

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryRelationshipType
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    RelationshipFilter,
    StorageReadResult,
    StorageWriteResult,
)
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.router.read_router import ReadRouter
from app.core.memory.storage.router.write_router import WriteRouter


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

    async def save_node(
        self,
        label: MemoryNodeLabel,
        data: dict,
    ) -> StorageWriteResult:
        return await self._write_router.save_node(label, data)

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
