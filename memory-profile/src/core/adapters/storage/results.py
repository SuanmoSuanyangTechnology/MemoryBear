"""Core-side result value objects of the read DSL.

They are deliberately separate from the ported host storage models
(:mod:`src.storage.models.dto`): the read engine compares and forwards plain
strings (``backend``), while the host layer speaks ``BackendType``/``MemoryNodeLabel``
enums. :mod:`src.core.adapters.storage.dsl_bridge` converts between the two.

Keeping them in their own module lets the bridge convert *into* these types
without importing the service module (which imports the bridge).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StorageItem:
    label: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StorageReadResult:
    items: Sequence[StorageItem] = field(default_factory=list)
    backend: str | None = None
    total: int = 0

    @classmethod
    def from_items(
        cls,
        items: Sequence[dict[str, Any]],
        *,
        label: str,
        backend: str,
    ) -> StorageReadResult:
        materialized = [StorageItem(label=label, data=item) for item in items]
        return cls(items=materialized, backend=backend, total=len(materialized))


def merge_read_results(results: Sequence[StorageReadResult]) -> StorageReadResult:
    """Merge per-label results; the backend is only reported when uniform."""

    items = [item for result in results for item in result.items]
    backend = (
        results[0].backend
        if results
        and results[0].backend is not None
        and all(result.backend == results[0].backend for result in results)
        else None
    )
    return StorageReadResult(
        items=items,
        backend=backend,
        total=sum(result.total for result in results),
    )


__all__ = ["StorageItem", "StorageReadResult", "merge_read_results"]
