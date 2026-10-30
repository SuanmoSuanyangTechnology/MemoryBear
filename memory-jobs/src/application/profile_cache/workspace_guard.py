"""Workspace eligibility at the application boundary.

The aggregate decides the rule and the domain error carries it; the application
only adds the reason for a workspace that does not exist at all, and the storage
type as a diagnostic detail. The reason tokens are a cross-service contract.
"""

from __future__ import annotations

from src.domain.shared.errors import WorkspaceNotEligible
from src.domain.shared.ids import WorkspaceId
from src.domain.workspace.model import Workspace


def ensure_workspace_can_generate(
    workspace: Workspace | None, workspace_id: WorkspaceId
) -> Workspace:
    if workspace is None:
        raise WorkspaceNotEligible(workspace_id, "workspace_not_found")
    try:
        workspace.ensure_generation_allowed()
    except WorkspaceNotEligible as exc:
        storage_type = (
            workspace.storage_type_raw
            or (workspace.storage.value if workspace.storage else None)
            or "unknown"
        )
        raise WorkspaceNotEligible(
            workspace_id, exc.reason, f"storage_type={storage_type}"
        ) from exc
    return workspace
