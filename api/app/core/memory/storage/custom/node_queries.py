from __future__ import annotations

from typing import Any

from app.core.memory.storage.enums import MemoryNodeLabel
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSort,
)
from app.core.memory.storage.service import get_storage_service


async def get_node_by_id(
    label: MemoryNodeLabel,
    node_id: str,
    projection: NodeProjection | None = None,
    node_sort: NodeSort | None = None,
) -> dict[str, Any] | None:
    """Fetch one node by id through the process-wide storage service."""
    result = await get_storage_service().get_node(
        label,
        NodeFilter.eq("id", node_id),
        projection,
        node_sort,
    )
    return result.items[0] if result.items else None
