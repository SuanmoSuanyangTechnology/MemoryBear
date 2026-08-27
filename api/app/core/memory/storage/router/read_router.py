import asyncio

from app.core.memory.storage.enums import MemoryNodeLabel, StorageBackendType
from app.core.memory.storage.models import NodeFilter, NodeProjection
from app.core.memory.storage.provider.factory import BackendFactory


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
    ) -> list[dict]:
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
        return [node for label_nodes in results for node in label_nodes]

    async def search_by_fulltext(
        self,
        labels: list[MemoryNodeLabel],
        node_filter: NodeFilter,
        text: str,
        limit: int,
        projection: NodeProjection | None = None,
    ) -> list[dict]:
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
        return [node for label_nodes in results for node in label_nodes]

    async def search_by_graph(self):
        pass

