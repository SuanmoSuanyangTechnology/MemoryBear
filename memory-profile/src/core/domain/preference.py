"""Persisted Coding-Agent preference bucket (``:Preference`` node).

Only the fields the end-user merge must validate and re-key are modelled; the
node's own writer owns the full schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class PreferenceNode(BaseModel):
    """A persisted Coding Agent preference bucket."""

    model_config = ConfigDict(extra="ignore")

    id: str
    end_user_id: str
    domain: str
    subject: str
    situation_key: str
    mode: list[str]
    preference_text: list[str]
    preference_text_all: str | None = None
    status: Literal["active", "inactive"] = "active"
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_aligned_items(self) -> PreferenceNode:
        if len(self.mode) != len(self.preference_text):
            raise ValueError("Preference node mode/text arrays have different lengths")
        return self


__all__ = ["PreferenceNode"]