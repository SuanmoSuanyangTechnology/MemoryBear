"""Centralized node query helpers.

Wraps the process-wide ``MemoryStorageService`` behind a small repository layer
so that filter/projection construction for common lookups lives in one place
instead of being scattered across callers.
"""

from __future__ import annotations

from app.core.memory.storage.enums import MemoryNodeLabel
from app.core.memory.storage.models import NodeFilter, NodeProjection
from app.core.memory.storage.service import get_storage_service


async def get_node_by_id(
        label: MemoryNodeLabel,
        node_id: str,
        projection: NodeProjection | None = None,
) -> dict | None:
    """Fetch a single node by its ``id`` field.

    Args:
        label: Node type to query.
        node_id: Value of the node's ``id`` field.
        projection: Optional projection; ``None`` returns the full node.

    Returns:
        The node's raw data dict, or ``None`` when no matching node exists.
    """
    service = get_storage_service()
    node_filter = NodeFilter.eq("id", node_id)
    result = await service.get_node(label, node_filter, projection, None)
    if not result.items:
        return None
    return result.items[0].data