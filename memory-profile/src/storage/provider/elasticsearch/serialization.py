"""Compatibility re-exports of the shared ``memory-storage-schema`` package.

Document normalization lives in :mod:`memory_storage_schema.serialization`
(the API-aligned shared package); this module keeps the historical import path
used by memory-profile's storage provider.
"""

from __future__ import annotations

from memory_storage_schema.serialization import (
    MAX_TEXT_FIELD_LENGTH,
    normalize_document,
    normalize_elasticsearch_document,
    route_embedding_field,
)

__all__ = [
    "MAX_TEXT_FIELD_LENGTH",
    "normalize_document",
    "normalize_elasticsearch_document",
    "route_embedding_field",
]
