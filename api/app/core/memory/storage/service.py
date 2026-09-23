from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Self

from app.core.memory.storage.enums import (
    BackendType,
    MemoryNodeLabel,
    MemoryRelationshipType,
)
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
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.neo4j.client import Neo4jClient
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

    @classmethod
    async def create_graph_write_only(cls) -> Self:
        """Create an isolated service owning only a Neo4j write client."""
        return cls(await BackendFactory.create_graph_write_only())

    async def get_workspace_memory_graph_statistics(
        self,
        end_user_ids: list[str],
    ) -> dict[str, int]:
        """聚合一批用户的情景、显性、情绪和隐性记忆。"""
        if not end_user_ids:
            return {
                "episodic_count": 0,
                "explicit_count": 0,
                "emotional_count": 0,
                "implicit_count": 0,
            }
        from app.core.memory.storage.custom.workspace_statistics import (
            get_elasticsearch_workspace_statistics,
            get_neo4j_fallback_statistics,
            get_neo4j_implicit_memory_count,
        )

        elastic_client = self._backend_factory.get_client(BackendType.ELASTIC)
        if not isinstance(elastic_client, ElasticClient):
            raise TypeError("ELASTIC backend must be an ElasticClient")
        neo4j_client = self._backend_factory.get_client(BackendType.NEO4J)
        if not isinstance(neo4j_client, Neo4jClient):
            raise TypeError("NEO4J backend must be a Neo4jClient")

        elastic_task = get_elasticsearch_workspace_statistics(
            elastic_client,
            end_user_ids,
        )
        implicit_task = get_neo4j_implicit_memory_count(
            neo4j_client,
            end_user_ids,
        )
        (elastic_statistics, present_ids), implicit_count = await asyncio.gather(
            elastic_task,
            implicit_task,
        )

        episodic_count = sum(
            item["episodic_count"] for item in elastic_statistics.values()
        )
        explicit_entity_count = sum(
            item["explicit_entity_count"] for item in elastic_statistics.values()
        )
        emotional_count = sum(
            item["emotional_count"] for item in elastic_statistics.values()
        )

        missing_ids = [
            end_user_id
            for end_user_id in end_user_ids
            if end_user_id not in present_ids
        ]
        if missing_ids:
            fallback = await get_neo4j_fallback_statistics(
                neo4j_client,
                missing_ids,
            )
            episodic_count += fallback["episodic_count"]
            explicit_entity_count += fallback["explicit_entity_count"]
            emotional_count += fallback["emotional_count"]

        return {
            "episodic_count": episodic_count,
            "explicit_count": episodic_count + explicit_entity_count,
            "emotional_count": emotional_count,
            "implicit_count": implicit_count,
        }

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
