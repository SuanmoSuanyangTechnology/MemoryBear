"""Workspace eligibility at the application boundary.

The aggregate decides the rule; the application translates it into the message
callers already receive (the reason strings are a cross-service contract).
"""

from __future__ import annotations

from src.domain.shared.errors import WorkspaceNotEligible
from src.domain.shared.ids import WorkspaceId
from src.domain.workspace.model import Workspace


def ensure_workspace_can_generate(
    workspace: Workspace | None, workspace_id: WorkspaceId
) -> Workspace:
    if workspace is None:
        raise ValueError(
            f"workspace {workspace_id} cannot generate cache: "
            "workspace_not_found (storage_type=unknown)"
        )
    try:
        workspace.ensure_generation_allowed()
    except WorkspaceNotEligible as exc:
        storage_type = (
            workspace.storage_type_raw
            or (workspace.storage.value if workspace.storage else None)
            or "unknown"
        )
        raise ValueError(
            f"workspace {workspace_id} cannot generate cache: {exc.reason} "
            f"(storage_type={storage_type})"
        ) from exc
    return workspace
