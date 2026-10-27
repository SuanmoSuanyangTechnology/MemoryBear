"""Request and response contracts for personality-anchor configuration."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class PersonalityAnchorConfig(BaseModel):
    """The complete set of editable personality-anchor settings."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    personality_anchor_enabled: StrictBool
    personality_anchor_auto_solidify: StrictBool
    personality_anchor_read_metadata: StrictBool
    personality_anchor_read_preferences: StrictBool
    personality_anchor_sync_cycle: Literal["daily", "weekly", "manual"]
    personality_anchor_base_principles: str = Field(
        ..., strict=True, min_length=1, max_length=4000,
    )

    @field_validator("personality_anchor_base_principles", mode="before")
    @classmethod
    def trim_base_principles(cls, value: object) -> object:
        """Validate the length after removing surrounding whitespace."""
        return value.strip() if isinstance(value, str) else value


class PersonalityAnchorConfigUpdate(PersonalityAnchorConfig):
    """Replace all six settings of a workspace-owned configuration."""

    config_id: UUID


class PersonalityAnchorConfigResponse(PersonalityAnchorConfig):
    """Return the saved settings and their configuration identity."""

    config_id: UUID
    is_default: bool
