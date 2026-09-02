"""
Provide a repository layer within this package to centralize data access
and query construction, avoiding scattered query logic across the codebase
and reducing maintenance complexity.
"""

from app.core.memory.storage.custom.node_queries import (
    get_active_entities_by_ids,
    get_node_by_id,
    get_user_entity_id,
    get_user_metadata,
    search_entities_by_name,
)

from app.core.memory.storage.custom.relationship_queries import (
    get_entity_pair_relations,
    get_user_sources_for_entities,
    search_related_entities,
)

__all__ = [
    "get_active_entities_by_ids",
    "get_entity_pair_relations",
    "get_node_by_id",
    "get_user_entity_id",
    "get_user_metadata",
    "get_user_sources_for_entities",
    "search_entities_by_name",
    "search_related_entities",
]