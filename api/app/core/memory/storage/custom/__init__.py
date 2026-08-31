"""
Provide a repository layer within this package to centralize data access
and query construction, avoiding scattered query logic across the codebase
and reducing maintenance complexity.
"""

from app.core.memory.storage.custom.node_queries import get_node_by_id

__all__ = ["get_node_by_id"]