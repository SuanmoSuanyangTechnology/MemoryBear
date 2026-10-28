"""Shared cross-module services.

Other modules obtain the services exported here; the repositories stay internal.
``EndUserService`` owns end-user rules (currently the workspace boundary),
``ModelConfigService`` answers "which model serves this workspace slot?",
``WorkspaceService`` owns the shared workspace row, and
``validate_workspace_row`` turns that row into the eligibility verdict. Keeping
these here means no module re-implements them, so a boundary check, a model
lookup or the workspace facts cannot drift between endpoints.
"""

from __future__ import annotations

from src.infrastructure.model_service import RerankHit
from src.modules.shared.end_user_service import EndUserRef, EndUserService
from src.modules.shared.model_config_service import (
    ChatModelProvider,
    ModelConfigService,
    ModelConfigurationError,
    ModelSlot,
    ResolvedModel,
    WorkspaceModelMemo,
)
from src.modules.shared.workspace_service import WorkspaceService
from src.modules.shared.workspace_validation import validate_workspace_row

__all__ = [
    "ChatModelProvider",
    "EndUserService",
    "EndUserRef",
    "ModelConfigService",
    "ModelConfigurationError",
    "ModelSlot",
    "RerankHit",
    "ResolvedModel",
    "WorkspaceModelMemo",
    "WorkspaceService",
    "validate_workspace_row",
]
