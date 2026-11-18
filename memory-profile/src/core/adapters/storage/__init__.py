"""Storage substrate for memory-profile read services.

This package is the **single place** where the read side touches storage:

* :mod:`~src.core.adapters.storage.indexes` — the index registry. It mirrors the
  API writer's Elasticsearch/Neo4j index definitions from a generated snapshot
  (``storage_index_contract.json``); index aliases, full-text/embedding fields,
  dimensions, default projections and the managed index/constraint definitions
  are managed here and nowhere else.
* :mod:`~src.core.adapters.storage.filters` / :mod:`~src.core.adapters.storage.projection`
  — the read DSL: backend-neutral filter and projection value objects plus the
  Neo4j/Elasticsearch compilers.
* :mod:`~src.core.adapters.storage.service` — the DSL executor. ``ReadStorage``
  routes a label to Elasticsearch or Neo4j (honouring ``ELASTIC_ONLY_LABELS``)
  and never opens its own pool; ``build_read_storage`` wires it onto the process
  runtime.

Read services (for example the retrieval adapter) consume the public names
below instead of importing the drivers, so a new read path gets the same DSL,
routing and index definitions with no extra plumbing.

This package is read-only: it contains no write query, no index creation and no
migration — the API host owns writes. ``scripts/check_import_boundaries.py``
enforces that.

Typical use::

    from src.core.adapters.storage import (
        NodeFilter, FilterCondition, NodeProjection, ReadStorage,
        build_read_storage, projection_for,
    )

    storage = build_read_storage(runtime)
    result = await storage.search_by_fulltext(
        {label: NodeFilter.all_of(FilterCondition(field="end_user_id", value=uid))},
        {label: projection_for(label)},
        query="...",
        limit=10,
    )
"""

from __future__ import annotations

from src.core.adapters.storage.filters import (
    DIALOGUE_WRITE_MODE,
    FilterCondition,
    FilterOperator,
    InCondition,
    NodeFilter,
    NodeFilterSpec,
    build_content_search_filters,
    compile_elasticsearch_filter,
    compile_neo4j_filter,
    merge_filters,
)
from src.core.adapters.storage.indexes import (
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_PROJECTION_SPECS,
    ELASTIC_ONLY_LABELS,
    ES_EMBEDDING_FIELD,
    ES_FULLTEXT_FIELDS,
    ES_INDEX_ALIAS,
    ES_INDEX_DEFINITIONS,
    ES_INDEX_SHARD_COUNT,
    INDEX_CONTRACT_LABELS,
    NEO4J_EMBEDDING_FIELD,
    NEO4J_FULLTEXT_INDEX,
    NEO4J_MANAGED_DEFINITIONS,
    SUPPORTED_EMBEDDING_DIMENSIONS,
    WRITER_CONSISTENCY_REPORT,
    es_embedding_field,
    labels_unknown_to_domain,
)
from src.core.adapters.storage.projection import (
    DEFAULT_PROJECTIONS,
    CoalesceField,
    NodeProjection,
    ProjectionField,
    RelationshipProjectionField,
    apply_elasticsearch_projection,
    compile_elasticsearch_projection,
    compile_neo4j_projection,
    compile_neo4j_relationship_projection,
    projection_for,
    projection_of,
)
from src.core.adapters.storage.service import (
    DEFAULT_READ_BACKEND,
    ReadStorage,
    RelationshipFilter,
    RelationshipPattern,
    StorageItem,
    StorageReadResult,
    build_read_storage,
    merge_read_results,
)

__all__ = [
    "DEFAULT_EMBEDDING_DIMENSION",
    "DEFAULT_PROJECTIONS",
    "DEFAULT_PROJECTION_SPECS",
    "DEFAULT_READ_BACKEND",
    "DIALOGUE_WRITE_MODE",
    "ELASTIC_ONLY_LABELS",
    "ES_EMBEDDING_FIELD",
    "ES_FULLTEXT_FIELDS",
    "ES_INDEX_ALIAS",
    "ES_INDEX_DEFINITIONS",
    "ES_INDEX_SHARD_COUNT",
    "INDEX_CONTRACT_LABELS",
    "NEO4J_EMBEDDING_FIELD",
    "NEO4J_FULLTEXT_INDEX",
    "NEO4J_MANAGED_DEFINITIONS",
    "SUPPORTED_EMBEDDING_DIMENSIONS",
    "WRITER_CONSISTENCY_REPORT",
    "CoalesceField",
    "FilterCondition",
    "FilterOperator",
    "InCondition",
    "NodeFilter",
    "NodeFilterSpec",
    "NodeProjection",
    "ProjectionField",
    "ReadStorage",
    "RelationshipFilter",
    "RelationshipPattern",
    "RelationshipProjectionField",
    "StorageItem",
    "StorageReadResult",
    "apply_elasticsearch_projection",
    "build_content_search_filters",
    "build_read_storage",
    "compile_elasticsearch_filter",
    "compile_elasticsearch_projection",
    "compile_neo4j_filter",
    "compile_neo4j_projection",
    "compile_neo4j_relationship_projection",
    "es_embedding_field",
    "labels_unknown_to_domain",
    "merge_filters",
    "merge_read_results",
    "projection_for",
    "projection_of",
]