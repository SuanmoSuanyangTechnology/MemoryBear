"""Shared Memory storage schema.

Single source of truth for Memory node labels, Elasticsearch index definitions
and document normalization. Consumed by both the API and the memory jobs service
so the two can never drift apart.
"""

from memory_storage_schema.definitions import (
    DEFAULT_EMBEDDING_DIMENSION,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_DIMS,
    EMBEDDING_FIELDS,
    FULLTEXT_FIELDS,
    INDEX_ALIAS_SUFFIX,
    INDEX_DEFINITIONS,
    INDEX_SHARD_COUNT,
    IndexDefinition,
    embedding_field_suffix,
    get_embedding_field_name,
    get_index_definition,
    get_index_name,
    is_supported_embedding_dimension,
    is_vector_label,
    validate_definition_registry,
)
from memory_storage_schema.labels import MemoryNodeLabel, MemoryNodeType
from memory_storage_schema.serialization import (
    MAX_TEXT_FIELD_LENGTH,
    normalize_document,
    normalize_elasticsearch_document,
    route_embedding_field,
)

__all__ = [
    "DEFAULT_EMBEDDING_DIMENSION",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_DIMS",
    "EMBEDDING_FIELDS",
    "FULLTEXT_FIELDS",
    "INDEX_ALIAS_SUFFIX",
    "INDEX_DEFINITIONS",
    "INDEX_SHARD_COUNT",
    "MAX_TEXT_FIELD_LENGTH",
    "IndexDefinition",
    "MemoryNodeLabel",
    "MemoryNodeType",
    "embedding_field_suffix",
    "get_embedding_field_name",
    "get_index_definition",
    "get_index_name",
    "is_supported_embedding_dimension",
    "is_vector_label",
    "normalize_document",
    "normalize_elasticsearch_document",
    "route_embedding_field",
    "validate_definition_registry",
]
