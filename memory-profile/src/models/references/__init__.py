"""Minimal read-only mappings for API-owned tables.

These models intentionally declare no foreign keys or relationships and are
never used as a migration metadata target.
"""

from src.models.references.end_user_model import EndUser, EndUserMerge
from src.models.references.memory_generation_model import (
    EndUserInfo,
    MemoryConfigRow,
    ModelConfig,
    WorkspaceDefaultModelPreset,
)
from src.models.references.memory_statistics_model import (
    Conversation,
    ForgetAudit,
    ImplicitEmotionsStorage,
    MemoryMessage,
    MemoryPerceptual,
    ShortTermMemory,
)
from src.models.references.workspace_model import Workspace

__all__ = [
    "Conversation",
    "EndUser",
    "EndUserInfo",
    "MemoryConfigRow",
    "ModelConfig",
    "WorkspaceDefaultModelPreset",
    "EndUserMerge",
    "ForgetAudit",
    "ImplicitEmotionsStorage",
    "MemoryMessage",
    "MemoryPerceptual",
    "ShortTermMemory",
    "Workspace",
]
