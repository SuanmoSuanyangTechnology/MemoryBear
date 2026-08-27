import asyncio

from app.core.memory.storage.enums import MemoryNodeLabel, StorageBackendType
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    StorageReadResult,
)
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

    async def search_by_embedding(
        self,
        labels: list[MemoryNodeLabel],
        node_filter: NodeFilter,
        embed: list,
        limit: int,
        projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        tasks = [
            self.backend_factory.get_read_client(
                label,
                StorageBackendType.VECTOR_MAIN_READ,
            ).search_by_embedding(
                label,
                node_filter,
                embed,
                limit,
                projection,
            )
            for label in labels
        ]
        results = await asyncio.gather(*tasks)
        return _merge_read_results(results)

    async def search_by_fulltext(
        self,
        labels: list[MemoryNodeLabel],
        node_filter: NodeFilter,
        text: str,
        limit: int,
        projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        tasks = [
            self.backend_factory.get_read_client(
                label,
                StorageBackendType.TEXT_MAIN_READ,
            ).search_by_fulltext(
                label,
                node_filter,
                text,
                limit,
                projection,
            )
            for label in labels
        ]
        results = await asyncio.gather(*tasks)
        return _merge_read_results(results)

    async def search_by_graph(self):
        pass

