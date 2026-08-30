from abc import ABC, abstractmethod

from typing import Self

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryRelationshipType
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSort,
    RelationshipFilter,
    StorageReadResult,
    StorageWriteResult,
)


class BaseClient(ABC):

    @classmethod
    @abstractmethod
    async def create(cls) -> Self:
        """Create and connect a client instance."""
        pass

    @staticmethod
    def verify_label(label: MemoryNodeLabel) -> None:
        if not isinstance(label, MemoryNodeLabel):
            raise KeyError(f"node type - {label} not supported")

    @classmethod
    def verify_input(cls, label: MemoryNodeLabel, data: dict) -> str:
        cls.verify_label(label)
        node_id = data.get("id")
        if node_id is None:
            raise ValueError("Memory Node id field is required")
        return node_id

    @abstractmethod
    async def health(self):
        pass

    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def close(self):
        pass

    @abstractmethod
    async def save_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
    ) -> StorageWriteResult:
        pass

    @abstractmethod
    async def update_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
            node_filter: NodeFilter,
    ) -> StorageWriteResult:
        pass

    @abstractmethod
    async def delete_node(
        self,
        label: MemoryNodeLabel,
        node_filter: NodeFilter,
        draft: bool = False,
    ) -> StorageWriteResult:
        pass

    @abstractmethod
    async def get_node(
        self,
        label: MemoryNodeLabel,
        node_filter: NodeFilter,
        projection: NodeProjection | None = None,
        node_sort: NodeSort | None = None,
    ) -> StorageReadResult:
        pass

    @abstractmethod
    async def search_by_fulltext(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            text: str,
            limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        pass

    @abstractmethod
    async def search_by_embedding(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            embed: list,
            limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        pass

    async def get_relationship(
        self,
        relationship_type: MemoryRelationshipType,
        rel_filter: RelationshipFilter,
        projection: NodeProjection | None = None,
        sort: NodeSort | None = None,
    ) -> StorageReadResult:
        raise NotImplementedError(
            f"{type(self).__name__} does not support relationship queries"
        )

    async def update_relationship(
        self,
        relationship_type: MemoryRelationshipType,
        data: dict,
        rel_filter: RelationshipFilter,
    ) -> StorageWriteResult:
        raise NotImplementedError(
            f"{type(self).__name__} does not support relationship updates"
        )

    async def delete_relationship(
        self,
        relationship_type: MemoryRelationshipType,
        rel_filter: RelationshipFilter,
    ) -> StorageWriteResult:
        raise NotImplementedError(
            f"{type(self).__name__} does not support relationship deletes"
        )
