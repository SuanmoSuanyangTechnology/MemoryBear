import asyncio
from collections.abc import Awaitable, Callable, Mapping

from app.core.logging_config import get_logger
from app.core.memory.storage.enums import (
    MemoryNodeLabel,
    StorageBackendType,
)
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSearchSpec,
    NodeSort,
    RelationshipFilter,
    RelationshipPattern,
    RelationshipProjection,
    RelationshipSort,
    StorageReadResult,
)
from app.core.memory.storage.models.projection import DEFAULT_PROJECTION
from app.core.memory.storage.provider.base import BaseClient
from app.core.memory.storage.provider.factory import BackendFactory

logger = get_logger()


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


async def _run_grouped_search(
        resolved: list[tuple[BaseClient, NodeSearchSpec]],
        invoke: Callable[
            [BaseClient, list[NodeSearchSpec]],
            Awaitable[list[StorageReadResult]],
        ],
) -> list[StorageReadResult]:
    groups: list[
        tuple[BaseClient, list[tuple[int, NodeSearchSpec]]]
    ] = []
    for index, (client, spec) in enumerate(resolved):
        for grouped_client, indexed_specs in groups:
            if grouped_client is client:
                indexed_specs.append((index, spec))
                break
        else:
            groups.append((client, [(index, spec)]))

    batches = await asyncio.gather(*(
        invoke(client, [spec for _, spec in indexed_specs])
        for client, indexed_specs in groups
    ))
    ordered: dict[int, StorageReadResult] = {}
    for (_, indexed_specs), batch in zip(groups, batches):
        if len(batch) != len(indexed_specs):
            raise RuntimeError(
                "storage batch search returned an unexpected result count"
            )
        for (index, _), result in zip(indexed_specs, batch):
            ordered[index] = result
    return [ordered[index] for index in range(len(resolved))]


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
            node_filters: Mapping[MemoryNodeLabel, NodeFilter],
            embed: list,
            pre_limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        resolved = [
            (
                self.backend_factory.get_read_client(
                    label,
                    StorageBackendType.VECTOR_MAIN_READ,
                ),
                NodeSearchSpec(
                    label=label,
                    node_filter=node_filter,
                    projection=projection or DEFAULT_PROJECTION[label],
                ),
            )
            for label, node_filter in node_filters.items()
        ]
        results = await _run_grouped_search(
            resolved,
            lambda client, specs: client.search_many_by_embedding(
                specs,
                embed,
                pre_limit,
            ),
        )
        return _merge_read_results(results)

    async def search_by_fulltext(
            self,
            node_filters: Mapping[MemoryNodeLabel, NodeFilter],
            text: str,
            pre_limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        resolved = [
            (
                self.backend_factory.get_read_client(
                    label,
                    StorageBackendType.TEXT_MAIN_READ,
                ),
                NodeSearchSpec(
                    label=label,
                    node_filter=node_filter,
                    projection=projection or DEFAULT_PROJECTION[label],
                ),
            )
            for label, node_filter in node_filters.items()
        ]
        results = await _run_grouped_search(
            resolved,
            lambda client, specs: client.search_many_by_fulltext(
                specs,
                text,
                pre_limit,
            ),
        )
        return _merge_read_results(results)

    async def search_relationships_by_graph(
            self,
            pattern: RelationshipPattern,
            rel_filter: RelationshipFilter,
            projection: RelationshipProjection | None = None,
            sort: RelationshipSort | None = None,
    ) -> StorageReadResult:
        client = self.backend_factory.get_relationship_client()
        return await client.get_relationship(
            pattern,
            rel_filter,
            projection,
            sort,
        )
