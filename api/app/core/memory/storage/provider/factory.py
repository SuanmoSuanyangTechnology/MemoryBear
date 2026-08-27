from __future__ import annotations

import asyncio
from typing import ClassVar, Self

from app.core.memory.storage.enums import (
    BackendType,
    MemoryNodeLabel,
    StorageBackendType,
)
from app.core.memory.storage.provider.base import BaseClient


class BackendFactory:
    BACKENDS: ClassVar[dict[BackendType, type[BaseClient]]] = {}
    FANOUTS: ClassVar[dict[StorageBackendType, list[BackendType]]] = {
        StorageBackendType.GRAPH_NODE: [BackendType.ELASTIC],
        StorageBackendType.TEXT_NODE: [BackendType.ELASTIC],
        StorageBackendType.VECTOR_NODE: [BackendType.ELASTIC],
    }

    _WRITE_DIMENSIONS = frozenset(
        {
            StorageBackendType.GRAPH_MAIN_WRITE,
            StorageBackendType.TEXT_MAIN_WRITE,
            StorageBackendType.VECTOR_MAIN_WRITE,
        }
    )
    _READ_DIMENSIONS = frozenset(
        {
            StorageBackendType.GRAPH_MAIN_READ,
            StorageBackendType.TEXT_MAIN_READ,
            StorageBackendType.VECTOR_MAIN_READ,
        }
    )

    _FANOUT_DIMENSIONS = frozenset(
        {
            StorageBackendType.GRAPH_NODE,
            StorageBackendType.TEXT_NODE,
            StorageBackendType.VECTOR_NODE,
        }
    )

    def __init__(self) -> None:
        self._clients: dict[BackendType, BaseClient] = {}
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls) -> Self:
        factory = cls()
        await factory.initialize()
        return factory

    @classmethod
    def register(cls, name: BackendType, obj: type[BaseClient]) -> None:
        cls.BACKENDS[name] = obj

    @classmethod
    def _ensure_builtin_backends_registered(cls) -> None:
        # Provider packages also self-register. These lazy imports make factory
        # startup deterministic without creating an import cycle at module load.
        if BackendType.NEO4J not in cls.BACKENDS:
            from app.core.memory.storage.provider.neo4j.client import Neo4jClient

            cls.BACKENDS.setdefault(BackendType.NEO4J, Neo4jClient)
        if BackendType.ELASTIC not in cls.BACKENDS:
            from app.core.memory.storage.provider.elasticsearch.client import (
                ElasticClient,
            )

            cls.BACKENDS.setdefault(BackendType.ELASTIC, ElasticClient)

    async def initialize(self) -> None:
        self._ensure_builtin_backends_registered()

        async with self._lock:
            missing = [kind for kind in BackendType if kind not in self._clients]
            if not missing:
                return

            created: dict[BackendType, BaseClient] = {}
            try:
                for backend_type in missing:
                    client_type = self.BACKENDS.get(backend_type)
                    if client_type is None:
                        raise RuntimeError(
                            f"Backend {backend_type.value} is not registered"
                        )
                    created[backend_type] = await client_type.create()
            except BaseException:
                if created:
                    await asyncio.gather(
                        *(client.close() for client in created.values()),
                        return_exceptions=True,
                    )
                raise

            self._clients.update(created)

    def get_client(self, backend_type: BackendType) -> BaseClient:
        try:
            return self._clients[backend_type]
        except KeyError:
            raise RuntimeError(
                "BackendFactory is not initialized; use "
                "`await BackendFactory.create()` first"
            ) from None

    def get_write_client(
        self,
        label: MemoryNodeLabel,
        dim: StorageBackendType,
    ) -> BaseClient:
        del label  # FEATURE: Reserved for future per-node overrides.
        if dim not in self._WRITE_DIMENSIONS:
            raise ValueError(f"Unsupported write storage dimension: {dim}")
        return self.get_client(BackendType.NEO4J)

    def get_read_client(
        self,
        label: MemoryNodeLabel,
        dim: StorageBackendType,
    ) -> BaseClient:
        del label  # FEATURE: Reserved for future per-node overrides.
        if dim not in self._READ_DIMENSIONS:
            raise ValueError(f"Unsupported read storage dimension: {dim}")
        return self.get_client(BackendType.ELASTIC)

    def get_node_clients(
        self,
        label: MemoryNodeLabel,
        dim: StorageBackendType,
    ) -> list[BaseClient]:
        del label  # FEATURE: Reserved for future per-node overrides.
        if dim not in self._FANOUT_DIMENSIONS:
            raise ValueError(f"Unsupported node storage dimension: {dim}")

        backend_types = self.FANOUTS.get(dim, [])
        return [self.get_client(backend_type) for backend_type in backend_types]

    def get_relationship_client(self) -> BaseClient:
        return self.get_client(BackendType.NEO4J)

    async def close(self) -> None:
        async with self._lock:
            clients = tuple(self._clients.values())
            self._clients.clear()

        if clients:
            await asyncio.gather(*(client.close() for client in clients))
