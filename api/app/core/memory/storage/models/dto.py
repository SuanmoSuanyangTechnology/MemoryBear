from __future__ import annotations

from typing import Any, Iterable, Self

from pydantic import BaseModel, Field

from app.core.memory.storage.enums import BackendType, MemoryNodeLabel, MemoryNodeType


class StorageResult(BaseModel):
    """Base result returned by storage providers and routers."""

    backend: BackendType | None = None


class StorageItem(BaseModel):
    label: MemoryNodeLabel | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class StorageWriteResult(StorageResult):
    """Normalized result for create, update, and delete operations."""

    affected_count: int = Field(default=0, ge=0)
    ids: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)


class GraphWriteResult(BaseModel):
    """Result of one committed memory-graph write transaction."""

    node_ids: dict[MemoryNodeType, list[str]] = Field(default_factory=dict)
    relationship_count: int = Field(default=0, ge=0)


class StorageReadResult(StorageResult):
    """Normalized result for get and search operations."""

    items: list[StorageItem] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)

    @classmethod
    def from_items(
            cls,
            items: Iterable[dict[str, Any]],
            label: MemoryNodeLabel | None = None,
            backend: BackendType | None = None,
    ) -> Self:
        materialized = [StorageItem(label=label, data=it) for it in items]
        return cls(
            backend=backend,
            items=materialized,
            total=len(materialized),
        )
