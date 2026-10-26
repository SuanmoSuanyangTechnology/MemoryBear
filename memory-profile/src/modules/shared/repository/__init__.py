"""Data access for the shared services."""

from __future__ import annotations

from src.modules.shared.repository.end_user import EndUserRepository
from src.modules.shared.repository.model_config import ModelConfigRepository
from src.modules.shared.repository.workspace import WorkspaceRepository

__all__ = ["EndUserRepository", "ModelConfigRepository", "WorkspaceRepository"]
