"""Minimal read-only mappings for API-owned tables.

These models intentionally declare no foreign keys or relationships and are
never used as a migration metadata target.
"""

from src.models.references.end_user_model import EndUser, EndUserMerge
from src.models.references.memory_implicit_model import ImplicitEmotionsStorage
from src.models.references.memory_statistics_model import (
    Conversation,
    ForgetAudit,
    MemoryMessage,
    MemoryPerceptual,
    ShortTermMemory,
)
from src.models.references.workspace_model import Workspace

__all__ = [
    "Conversation",
    "EndUser",
    "EndUserMerge",
    "ForgetAudit",
    "ImplicitEmotionsStorage",
    "MemoryMessage",
    "MemoryPerceptual",
    "ShortTermMemory",
    "Workspace",
]
