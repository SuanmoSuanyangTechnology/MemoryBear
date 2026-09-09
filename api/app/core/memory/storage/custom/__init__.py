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
from app.core.memory.storage.custom.end_user_delete import (
    DeletedEndUserNodeIdentity,
    EndUserDeleteOutboxError,
    delete_end_user_memory_nodes,
)
from app.core.memory.storage.custom.end_user_merge import (
    EndUserMergeNodeIdentity,
    EndUserMergeOutboxError,
    EndUserMergePrimaryError,
    EndUserMergeStats,
    merge_end_user_memory_nodes,
)
from app.core.memory.storage.custom.community_mutations import (
    CommunityMutationOutboxError,
    CommunityMutationWriter,
    CommunityNodeIdentity,
    CommunityReconcileStats,
)
from app.core.memory.storage.custom.topology_score import compute_topology_score
from app.core.memory.storage.custom.forget_recovery import (
    ForgetRecoveryTarget,
    recover_forgotten_node_by_element_id,
    resolve_forget_recovery_target,
)
from app.core.memory.storage.custom.reflection_entity_updates import (
    merge_entity_description,
    rename_entity,
    update_entity_name_embedding,
)
from app.core.memory.storage.custom.reflection_mutations import (
    append_user_info,
    create_unresolved_entity,
    create_unresolved_relationship,
    create_unresolved_statement_entity_edge,
    delete_alias_nodes,
    drop_alias_belongs_edges,
    merge_alias_properties,
    merge_entities,
    patch_entity_metadata,
    redirect_alias_edges,
    resolve_statement,
)

__all__ = [
    "AutomaticForgetOutboxError",
    "CommunityMutationOutboxError",
    "CommunityMutationWriter",
    "CommunityNodeIdentity",
    "CommunityReconcileStats",
    "DeletedEndUserNodeIdentity",
    "EndUserDeleteOutboxError",
    "EndUserMergeNodeIdentity",
    "EndUserMergeOutboxError",
    "EndUserMergePrimaryError",
    "EndUserMergeStats",
    "ForgottenNodeIdentity",
    "ForgetRecoveryTarget",
    "ManualDeleteTarget",
    "append_user_info",
    "compute_topology_score",
    "create_unresolved_entity",
    "create_unresolved_relationship",
    "create_unresolved_statement_entity_edge",
    "delete_alias_nodes",
    "delete_end_user_memory_nodes",
    "delete_manual_node_by_element_id",
    "drop_alias_belongs_edges",
    "resolve_manual_delete_target",
    "recover_forgotten_node_by_element_id",
    "resolve_forget_recovery_target",
    "get_active_entities_by_ids",
    "get_entity_pair_relations",
    "get_node_by_id",
    "get_user_entity_id",
    "get_user_metadata",
    "get_user_sources_for_entities",
    "merge_alias_properties",
    "merge_end_user_memory_nodes",
    "merge_entities",
    "merge_entity_description",
    "patch_entity_metadata",
    "redirect_alias_edges",
    "rename_entity",
    "resolve_statement",
    "search_entities_by_name",
    "search_related_entities",
    "soft_delete_forgetting_nodes",
    "update_entity_name_embedding",
    "update_user_entity_aliases",
]
