"""节点主写与 outbox 投影事件入队；关系写固定走 Neo4j 且不产生事件。"""

from __future__ import annotations

from app.core.memory.storage.enums import (
    MemoryNodeLabel,
    MemoryNodeType,
    MemoryRelationshipType,
    StorageBackendType,
)
from app.core.memory.storage.models import (
    GraphWriteResult,
    MemoryGraphWriteCommand,
    NodeFilter,
    RelationshipFilter,
    StorageWriteResult,
)
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.base import BaseClient
from app.core.memory.storage.provider.factory import BackendFactory


class WriteRouter:
    def __init__(
        self,
        backend_factory: BackendFactory,
        *,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self.backend_factory = backend_factory
        self.outbox_repository = outbox_repository

    async def save_node(
        self,
        label: MemoryNodeLabel,
        data: dict,
    ) -> StorageWriteResult:
        result = await self._write_client(label).save_node(label, data)
        await self._enqueue_result(label, result, OutboxOperation.UPSERT)
        return result

    async def save_memory_graph(
        self,
        command: MemoryGraphWriteCommand,
    ) -> GraphWriteResult:
        """Commit one extracted memory graph, then enqueue its node projections."""
        client = self.backend_factory.get_graph_write_client()
        result = await client.save_memory_graph(command)
        await self._enqueue_graph_result(result)
        return result

    async def save_memory_summaries(self, summaries) -> GraphWriteResult:
        """Commit summary nodes and edges, then enqueue summary projections."""
        client = self.backend_factory.get_graph_write_client()
        result = await client.save_memory_summaries(summaries)
        await self._enqueue_graph_result(result)
        return result

    async def update_node(
        self,
        label: MemoryNodeLabel,
        data: dict,
        node_filter: NodeFilter,
    ) -> StorageWriteResult:
        result = await self._write_client(label).update_node(
            label,
            data,
            node_filter,
        )
        await self._enqueue_result(label, result, OutboxOperation.UPSERT)
        return result

    async def delete_node(
        self,
        label: MemoryNodeLabel,
        node_filter: NodeFilter,
        draft: bool = False,
    ) -> StorageWriteResult:
        result = await self._write_client(label).delete_node(
            label,
            node_filter,
            draft,
        )
        operation = (
            OutboxOperation.DRAFT_DELETE if draft else OutboxOperation.DELETE
        )
        await self._enqueue_result(label, result, operation)
        return result

    async def save_relationship(
        self,
        relationship_type: MemoryRelationshipType,
        source: str,
        target: str,
        data: dict,
    ) -> StorageWriteResult:
        return await self.backend_factory.get_relationship_client().save_relationship(
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
        return await self.backend_factory.get_relationship_client().update_relationship(
            relationship_type,
            data,
            rel_filter,
        )

    async def delete_relationship(
        self,
        relationship_type: MemoryRelationshipType,
        rel_filter: RelationshipFilter,
    ) -> StorageWriteResult:
        return await self.backend_factory.get_relationship_client().delete_relationship(
            relationship_type,
            rel_filter,
        )

    def _write_client(self, label: MemoryNodeLabel) -> BaseClient:
        return self.backend_factory.get_write_client(
            label,
            StorageBackendType.GRAPH_MAIN_WRITE,
        )

    async def _enqueue_result(
        self,
        label: MemoryNodeLabel,
        result: StorageWriteResult,
        operation: OutboxOperation,
    ) -> None:
        node_ids = list(dict.fromkeys(result.ids))
        if result.affected_count != len(node_ids):
            raise ValueError(
                "Storage write result must include one unique id per affected node"
            )
        if not node_ids:
            return
        await enqueue_events(
            [
                OutboxEventInput(
                    label=label,
                    node_id=node_id,
                    operation=operation,
                )
                for node_id in node_ids
            ],
            repository=self.outbox_repository,
        )

    async def _enqueue_graph_result(self, result: GraphWriteResult) -> None:
        events = [
            OutboxEventInput(
                label=label,
                node_id=node_id,
                operation=OutboxOperation.UPSERT,
            )
            for label, ids in result.node_ids.items()
            for node_id in dict.fromkeys(ids)
        ]
        await enqueue_events(events, repository=self.outbox_repository)
