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
    update_user_entity_aliases,
)

from app.core.memory.storage.custom.relationship_queries import (
    get_entity_pair_relations,
    get_user_sources_for_entities,
    search_related_entities,
)
from app.core.memory.storage.custom.automatic_forgetting import (
    AutomaticForgetOutboxError,
    ForgottenNodeIdentity,
    soft_delete_forgetting_nodes,
)
from app.core.memory.storage.custom.manual_node_delete import (
    ManualDeleteTarget,
    delete_manual_node_by_element_id,
    resolve_manual_delete_target,
)
from app.core.memory.storage.custom.topology_score import compute_topology_score
from app.core.memory.storage.custom.forget_recovery import (
    ForgetRecoveryTarget,
    recover_forgotten_node_by_element_id,
    resolve_forget_recovery_target,
)

__all__ = [
    "AutomaticForgetOutboxError",
    "ForgottenNodeIdentity",
    "ForgetRecoveryTarget",
    "ManualDeleteTarget",
    "compute_topology_score",
    "delete_manual_node_by_element_id",
    "resolve_manual_delete_target",
    "recover_forgotten_node_by_element_id",
    "resolve_forget_recovery_target",
    "get_active_entities_by_ids",
    "get_entity_pair_relations",
    "get_node_by_id",
    "get_user_entity_id",
    "get_user_metadata",
    "get_user_sources_for_entities",
    "search_entities_by_name",
    "search_related_entities",
    "soft_delete_forgetting_nodes",
    "update_user_entity_aliases",
]