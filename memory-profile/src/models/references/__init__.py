"""Minimal read-only mappings for API-owned tables.

These models intentionally declare no foreign keys or relationships and are
never used as a migration metadata target.
"""

from src.models.references.end_user_model import EndUser, EndUserMerge
from src.models.references.file_metadata_model import FileMetadata
from src.models.references.memory_config_model import MemoryConfigRow
from src.models.references.memory_generation_model import (
    EndUserInfo,
    ModelConfig,
    WorkspaceDefaultModelPreset,
)
from src.models.references.memory_reembed_model import (
    MemoryReembedJobRow,
    MemoryReembedJobUserRow,
)
from src.models.references.conversation_model import Conversation
from src.models.references.implicit_emotions_storage_model import ImplicitEmotionsStorage
from src.models.references.memory_statistics_model import (
    DialogueEmotionRaw,
    ForgetAudit,
    MemoryIncrement,
    MemoryMessage,
    MemoryPerceptual,
    ShortTermMemory,
)
from src.models.references.ontology_scene_model import OntologySceneRow
from src.models.references.workspace_model import Workspace

__all__ = [
    "Conversation",
    "DialogueEmotionRaw",
    "MemoryIncrement",
    "EndUser",
    "EndUserInfo",
    "MemoryConfigRow",
    "OntologySceneRow",
    "ModelConfig",
    "WorkspaceDefaultModelPreset",
    "EndUserMerge",
    "ForgetAudit",
    "FileMetadata",
    "ImplicitEmotionsStorage",
    "MemoryMessage",
    "MemoryPerceptual",
    "ShortTermMemory",
    "Workspace",
]

from src.models.references.memory_display_model import MemoryDisplayRecord, MemoryEngineDisplayEvent

__all__ += ["MemoryDisplayRecord", "MemoryEngineDisplayEvent"]
__all__ += ["MemoryReembedJobRow", "MemoryReembedJobUserRow"]
