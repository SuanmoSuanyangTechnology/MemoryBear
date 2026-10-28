"""Workspace eligibility check shared by the worker's cache-generation units.

``WorkspaceRepository.workspace_row`` returns the raw row; this is the one rule
set that decides whether cache generation may run against it. The producer
(memory-profile) runs the same predicate before publishing a task, so the
returned reasons are part of the cross-service contract:
``workspace_not_found``, ``workspace_inactive``, ``unsupported_storage_type``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def validate_workspace_row(
    workspace: Mapping[str, Any] | None,
) -> tuple[bool, str, str | None]:
    """Return ``(valid, reason, storage_type)`` for an already-loaded workspace."""

    if workspace is None:
        return False, "workspace_not_found", None
    raw_storage_type = workspace["storage_type"]
    if not workspace["is_active"]:
        return False, "workspace_inactive", raw_storage_type
    storage_type = "neo4j" if raw_storage_type is None else str(raw_storage_type).lower()
    if storage_type != "neo4j":
        return False, "unsupported_storage_type", storage_type or None
    return True, "valid", storage_type
