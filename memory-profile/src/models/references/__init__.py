"""Minimal read-only mappings for API-owned tables.

These models intentionally declare no foreign keys or relationships and are
never used as a migration metadata target.
"""

from src.models.references.end_user_model import EndUser, EndUserMerge
from src.models.references.memory_config_model import MemoryConfigRow
from src.models.references.memory_generation_model import (
    EndUserInfo,
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
from src.models.references.ontology_scene_model import OntologySceneRow
from src.models.references.workspace_model import Workspace

__all__ = [
    "Conversation",
    "EndUser",
    "EndUserInfo",
    "MemoryConfigRow",
    "OntologySceneRow",
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
