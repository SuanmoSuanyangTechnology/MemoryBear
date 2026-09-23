"""Request and response schemas for Coding Agent preference configuration."""

import uuid

from pydantic import BaseModel, ConfigDict, model_validator


class PreferenceConfigUpdate(BaseModel):
    """Update one workspace-owned preference configuration."""

    model_config = ConfigDict(extra="forbid")
    config_id: uuid.UUID
    preference_engine_enabled: bool | None = None
    custom_keywords: list[str] | None = None

    @model_validator(mode="after")
    def require_update_intent(self):
        if (
            self.preference_engine_enabled is None
            and self.custom_keywords is None
        ):
            raise ValueError("at least one preference config update field is required")
        return self


class PreferenceConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_id: uuid.UUID
    preference_engine_enabled: bool
    default_keywords: list[str]
    custom_keywords: list[str]
    effective_keywords: list[str]
