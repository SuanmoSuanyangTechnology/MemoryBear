"""Host-owned table mappings.

Every model here is a reference: the DDL and migrations live with the service
that owns the schema. Import from this package rather than a submodule so a table
can move between modules without touching callers.
"""

from __future__ import annotations

from src.models.end_user_model import EndUser, EndUserMerge
from src.models.memory_generation_model import (
    EndUserInfo,
    MemoryConfigRow,
    ModelConfig,
    WorkspaceDefaultModelPreset,
)
from src.models.workspace_model import Workspace

__all__ = [
    "EndUser",
    "EndUserInfo",
    "EndUserMerge",
    "MemoryConfigRow",
    "ModelConfig",
    "Workspace",
    "WorkspaceDefaultModelPreset",
]
