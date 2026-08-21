"""Chunk metadata merge behavior copied from the legacy chunk package."""

from copy import deepcopy
from typing import Any

SYSTEM_METADATA_KEYS = {
    "doc_id",
    "file_id",
    "file_name",
    "file_created_at",
    "document_id",
    "knowledge_id",
    "sort_id",
    "status",
    "chunk_type",
    "parent_id",
    "question",
    "answer",
    "source_chunk_id",
}


def merge_parser_metadata(
    system_metadata: dict[str, Any],
    parser_item: dict | None,
) -> dict[str, Any]:
    parser_metadata: dict[str, Any] = {}
    if isinstance(parser_item, dict) and isinstance(parser_item.get("metadata"), dict):
        parser_metadata = deepcopy(parser_item["metadata"])
    for key in SYSTEM_METADATA_KEYS:
        parser_metadata.pop(key, None)
    parser_metadata.update(system_metadata)
    return parser_metadata


__all__ = ["SYSTEM_METADATA_KEYS", "merge_parser_metadata"]
