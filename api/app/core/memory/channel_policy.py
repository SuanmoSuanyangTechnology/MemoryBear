"""Supported memory channels for the delivery build."""

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.memory.enums import StorageType


def require_neo4j_memory(storage_type: str | None) -> StorageType:
    """Reject unsupported channels before performing I/O or dispatching tasks."""
    if storage_type is None or storage_type == "":
        return StorageType.NEO4J
    if str(storage_type).lower() != StorageType.NEO4J.value:
        raise BusinessException(
            "This delivery build only supports Neo4j memory storage",
            BizCode.INVALID_PARAMETER,
        )
    return StorageType.NEO4J
