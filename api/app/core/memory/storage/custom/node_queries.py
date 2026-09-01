"""Centralized node query helpers.

Wraps the process-wide ``MemoryStorageService`` behind a small repository layer
so that filter/projection construction for common lookups lives in one place
instead of being scattered across callers.
"""

from __future__ import annotations

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryNodeType
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
)
from app.core.memory.storage.service import get_storage_service
from app.utils.redis_cache import redis_cache


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


async def get_user_entity_id(end_user_id: str) -> str | None:
    """Return the active user entity ID for an end user, if one exists."""
    service = get_storage_service()
    result = await service.get_node(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.all_of(
            FilterCondition(field="end_user_id", value=end_user_id),
            FilterCondition(field="entity_type", value="用户"),
            FilterCondition(
                field="delete_at",
                operator=FilterOperator.EXISTS,
                value=False,
            ),
        ),
        NodeProjection.of("id"),
        None,
    )
    if not result.items:
        return None

    node_id = result.items[0].data.get("id")
    return str(node_id) if node_id is not None else None


@redis_cache(ttl=60, prefix="memory", id_arg="end_user_id")
async def get_user_metadata(end_user_id: str) -> dict:
    """Return the active user entity fields used to build memory metadata."""
    service = get_storage_service()
    result = await service.get_node(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.all_of(
            FilterCondition(field="end_user_id", value=end_user_id),
            FilterCondition(field="entity_type", value="用户"),
            FilterCondition(
                field="delete_at",
                operator=FilterOperator.EXISTS,
                value=False,
            ),
        ),
        NodeProjection.of(
            "description",
            "aliases",
            "anchors",
            "beliefs_or_stances",
            "core_facts",
            "event_timeline",
            "goals",
            "interests",
            "relations",
            "traits",
            "id",
        ),
        None,
    )
    return result.items[0].data if result.items else {}


async def get_active_entities_by_ids(entity_ids: list[str]) -> list[dict]:
    """Return active entities needed when building relationship memories."""
    if not entity_ids:
        return []

    service = get_storage_service()
    result = await service.get_node(
        MemoryNodeType.EXTRACTED_ENTITY,
        NodeFilter.all_of(
            FilterCondition(
                field="id",
                operator=FilterOperator.IN,
                value=entity_ids,
            ),
            FilterCondition(
                field="delete_at",
                operator=FilterOperator.EXISTS,
                value=False,
            ),
        ),
        NodeProjection.of("id", "name", "description"),
        None,
    )
    return [item.data for item in result.items]


async def search_entities_by_name(
        end_user_id: str,
        name: str,
        limit: int = 10,
) -> list[dict]:
    """Search active entities by name within one end user's tenant."""
    service = get_storage_service()
    result = await service.search_by_fulltext(
        node_filters={
            MemoryNodeType.EXTRACTED_ENTITY: NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                FilterCondition(
                    field="delete_at",
                    operator=FilterOperator.EXISTS,
                    value=False,
                ),
            ),
        },
        projection=NodeProjection.of("id", "name", "entity_type"),
        text=name,
        pre_limit=limit,
    )
    return [item.data for item in result.items]
