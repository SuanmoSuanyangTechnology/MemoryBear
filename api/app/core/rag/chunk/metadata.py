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


def merge_parser_metadata(system_metadata: dict[str, Any], parser_item: dict | None) -> dict[str, Any]:
    """Merge parser metadata without allowing it to override system-owned fields."""
    parser_metadata: dict[str, Any] = {}
    if isinstance(parser_item, dict) and isinstance(parser_item.get("metadata"), dict):
        parser_metadata = deepcopy(parser_item["metadata"])

    for key in SYSTEM_METADATA_KEYS:
        parser_metadata.pop(key, None)

    merged = parser_metadata
    merged.update(system_metadata)
    return merged
