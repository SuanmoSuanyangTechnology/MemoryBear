import asyncio

from app.core.memory.storage.enums import (
    MemoryNodeLabel,
    MemoryRelationshipType,
    StorageBackendType, MemoryNodeType,
)
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    NodeSort,
    RelationshipFilter,
    StorageReadResult,
)
from app.core.memory.storage.models.projection import DEFAULT_PROJECTION
from app.core.memory.storage.provider.factory import BackendFactory


def _merge_read_results(
        results: list[StorageReadResult],
) -> StorageReadResult:
    items = [item for result in results for item in result.items]
    backend = (
        results[0].backend
        if results
           and results[0].backend is not None
           and all(result.backend == results[0].backend for result in results)
        else None
    )
    return StorageReadResult(
        backend=backend,
        items=items,
        total=sum(result.total for result in results),
    )


class ReadRouter:
    def __init__(self, backend_factory: BackendFactory) -> None:
        self.backend_factory = backend_factory

    async def get_node(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            projection: NodeProjection | None = None,
            node_sort: NodeSort | None = None,
    ) -> StorageReadResult:
        client = self.backend_factory.get_read_client(
            label,
            StorageBackendType.GRAPH_MAIN_READ,
        )
        return await client.get_node(label, node_filter, projection, node_sort)

    async def search_by_embedding(
            self,
            labels: list[MemoryNodeLabel],
            node_filter: NodeFilter,
            embed: list,
            pre_limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        tasks = []
        for label in labels:
            if label == MemoryNodeType.DIALOGUE:
                node_filter = NodeFilter(
                    logic=node_filter.logic,
                    conditions=tuple(
                        list(node_filter.conditions)
                        + [
                            FilterCondition(
                                field="write_mode",
                                operator=FilterOperator.EQ,
                                value="fast",
                            )
                        ]
                    ),
                )

            tasks.append(
                self.backend_factory.get_read_client(
                    label,
                    StorageBackendType.VECTOR_MAIN_READ,
                ).search_by_embedding(
                    label,
                    node_filter,
                    embed,
                    pre_limit,
                    projection if projection else DEFAULT_PROJECTION[label]
                )
            )
        results: list[StorageReadResult] = list(await asyncio.gather(*tasks))
        return _merge_read_results(results)

    async def search_by_fulltext(
            self,
            labels: list[MemoryNodeLabel],
            node_filter: NodeFilter,
            text: str,
            pre_limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        tasks = []
        for label in labels:
            if label == MemoryNodeType.DIALOGUE:
                node_filter = NodeFilter(
                    logic=node_filter.logic,
                    conditions=tuple(
                        list(node_filter.conditions)
                        + [
                            FilterCondition(
                                field="write_mode",
                                operator=FilterOperator.EQ,
                                value="fast",
                            )
                        ]
                    ),
                )

            tasks.append(
                self.backend_factory.get_read_client(
                    label,
                    StorageBackendType.TEXT_MAIN_READ,
                ).search_by_fulltext(
                    label,
                    node_filter,
                    text,
                    pre_limit,
                    projection if projection else DEFAULT_PROJECTION[label]
                )
            )
        results: list[StorageReadResult] = list(await asyncio.gather(*tasks))
        return _merge_read_results(results)

    async def search_relationships_by_graph(
            self,
            relationship_type: MemoryRelationshipType,
            rel_filter: RelationshipFilter,
            projection: NodeProjection | None = None,
            sort: NodeSort | None = None,
    ) -> StorageReadResult:
        client = self.backend_factory.get_relationship_client()
        return await client.get_relationship(
            relationship_type,
            rel_filter,
            projection,
            sort,
        )
