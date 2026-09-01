from pydantic import BaseModel, ConfigDict

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryRelationshipType


class RelationshipPattern(BaseModel):
    """Graph pattern used to select relationships and endpoint nodes."""

    model_config = ConfigDict(frozen=True)

    relationship_type: MemoryRelationshipType | None = None
    directed: bool = True
    source_label: MemoryNodeLabel | None = None
    target_label: MemoryNodeLabel | None = None
