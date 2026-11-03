"""Request and response contracts for personality-anchor configuration."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, field_validator


class PersonalityAnchorConfig(BaseModel):
    """The complete set of editable personality-anchor settings."""

    personality_anchor_enabled: StrictBool
    personality_anchor_auto_solidify: StrictBool
    personality_anchor_read_metadata: StrictBool
    personality_anchor_read_preferences: StrictBool
    personality_anchor_sync_cycle: Literal["daily", "weekly", "manual"]
    personality_anchor_base_principles: str = Field(
        ..., strict=True, max_length=4000,
    )


class PersonalityAnchorConfigUpdate(PersonalityAnchorConfig):
    """Replace all six settings of a workspace-owned configuration."""

    config_id: UUID

    @field_validator("personality_anchor_base_principles", mode="before")
    @classmethod
    def trim_base_principles(cls, value: object) -> object:
        """Trim update input and reject blank base principles."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("基础身份与原则不能为空")
        return value


class PersonalityAnchorConfigResponse(PersonalityAnchorConfig):
    """Return the saved settings and their configuration identity."""

    config_id: UUID
    is_default: bool
