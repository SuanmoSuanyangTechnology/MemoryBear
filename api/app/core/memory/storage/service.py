from __future__ import annotations

from typing import Self

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryRelationshipType
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSort,
    RelationshipFilter,
    StorageReadResult,
)
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.router.read_router import ReadRouter


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

    async def search_relationships_by_graph(
        self,
        relationship_type: MemoryRelationshipType,
        rel_filter: RelationshipFilter,
        projection: NodeProjection | None = None,
        sort: NodeSort | None = None,
    ) -> StorageReadResult:
        return await self._read_router.search_relationships_by_graph(
            relationship_type,
            rel_filter,
            projection,
            sort,
        )

    async def close(self) -> None:
        await self._backend_factory.close()
