from __future__ import annotations

from typing import Any, Iterable, Self

from pydantic import BaseModel, Field

from app.core.memory.storage.enums import BackendType


class StorageResult(BaseModel):
    """Base result returned by storage providers and routers."""

    backend: BackendType | None = None


class StorageWriteResult(StorageResult):
    """Normalized result for create, update, and delete operations."""

    affected_count: int = Field(default=0, ge=0)
    ids: list[str] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)


class StorageReadResult(StorageResult):
    """Normalized result for get and search operations."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)

    @classmethod
    def from_items(
            cls,
            items: Iterable[dict[str, Any]],
            backend: BackendType | None = None,
    ) -> Self:
        materialized = list(items)
        return cls(
            backend=backend,
            items=materialized,
            total=len(materialized),
        )
