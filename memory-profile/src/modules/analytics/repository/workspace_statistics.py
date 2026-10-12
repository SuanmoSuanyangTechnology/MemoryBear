"""Workspace statistics snapshots over the shared Memory Redis cache."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.common.redis_cache import (
    RedisCacheDecodeError,
    RedisCacheUnavailableError,
    zrange_json,
)
from src.infrastructure.redis import RedisManager


class WorkspaceStatisticsRepositoryError(RuntimeError):
    """Base error exposed by this repository to the service layer."""


class WorkspaceStatisticsUnavailableError(WorkspaceStatisticsRepositoryError):
    pass


class WorkspaceStatisticsCorruptError(WorkspaceStatisticsRepositoryError):
    pass


class WorkspaceStatisticsRepository:
    def __init__(self, redis: RedisManager) -> None:
        self.redis = redis

    async def workspace_snapshots(self, workspace_id: UUID) -> list[dict[str, Any]]:
        key = f"cache:memory:workspace_statistics:v1:{workspace_id}"
        try:
            values = await zrange_json(
                key,
                0,
                1,
                desc=True,
                client_provider=self.redis.client,
            )
        except RedisCacheUnavailableError as exc:
            raise WorkspaceStatisticsUnavailableError(
                "workspace statistics cache unavailable"
            ) from exc
        except RedisCacheDecodeError as exc:
            raise WorkspaceStatisticsCorruptError(
                "workspace statistics cache contains invalid JSON"
            ) from exc

        snapshots: list[dict[str, Any]] = []
        for index, value in enumerate(values):
            if not self._is_snapshot(value):
                raise WorkspaceStatisticsCorruptError(
                    f"workspace statistics snapshot schema invalid: index={index}"
                )
            snapshots.append(value)
        return snapshots

    @staticmethod
    def _is_snapshot(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        statistics = value.get("statistics")
        if not isinstance(statistics, list) or not all(
            isinstance(item, dict) for item in statistics
        ):
            return False
        for field in ("total_count", "total_users", "generated_at"):
            field_value = value.get(field)
            if isinstance(field_value, bool) or not isinstance(field_value, int):
                return False
        return True
