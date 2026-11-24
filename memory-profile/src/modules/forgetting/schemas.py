"""Manual forgetting input, preserving the legacy bounds and defaults."""

from pydantic import BaseModel, ConfigDict, Field


class ForgettingTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    end_user_id: str
    max_merge_batch_size: int = Field(100, ge=1, le=1000)
    min_days_since_access: int = Field(30, ge=1, le=365)
