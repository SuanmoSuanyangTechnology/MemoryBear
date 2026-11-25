"""Compatibility re-exports of the shared ``memory-storage-schema`` package.

The Elasticsearch index registry is owned by :mod:`memory_storage_schema` (the
API-aligned shared package). This module keeps memory-profile's historical import
path working, so callers do not need to know where the definitions live.
"""

from __future__ import annotations

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

__all__ = [
    "DEFAULT_EMBEDDING_DIMENSION",
    "EMBEDDING_DIMENSIONS",
    "EMBEDDING_DIMS",
    "EMBEDDING_FIELDS",
    "FULLTEXT_FIELDS",
    "INDEX_ALIAS_SUFFIX",
    "INDEX_DEFINITIONS",
    "INDEX_SHARD_COUNT",
    "IndexDefinition",
    "embedding_field_suffix",
    "get_embedding_field_name",
    "get_index_definition",
    "get_index_name",
    "is_supported_embedding_dimension",
    "is_vector_label",
    "validate_definition_registry",
]
