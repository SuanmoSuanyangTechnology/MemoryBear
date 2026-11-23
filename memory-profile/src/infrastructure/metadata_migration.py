"""Shared Redis breaker operations for metadata migration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from redis.asyncio import Redis
from task_bridge.metadata_migration import metadata_migration_circuit_breaker_key


class MetadataMigrationCircuitBreaker:
    def __init__(self, client_provider: Callable[[], Awaitable[Redis]]) -> None:
        self._client_provider = client_provider

    async def clear(self, workspace_id: UUID | str) -> None:
        client = await self._client_provider()
        await client.delete(metadata_migration_circuit_breaker_key(str(workspace_id)))
