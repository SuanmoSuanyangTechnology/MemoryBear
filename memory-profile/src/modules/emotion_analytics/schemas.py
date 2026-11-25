"""Emotion analytics HTTP DTOs."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EmotionTagsRequest(BaseModel):
    end_user_id: str = Field(..., min_length=1)
    start_date: str | None = None
    end_date: str | None = None
    limit: int = Field(10, ge=1, le=100)


class EmotionWordcloudRequest(BaseModel):
    end_user_id: str = Field(..., min_length=1)
    limit: int = Field(50, ge=1, le=200)


class EmotionHealthRequest(BaseModel):
    end_user_id: str = Field(..., min_length=1)
    time_range: Literal["7d", "30d", "90d"] = "30d"


class EmotionSuggestionsRequest(BaseModel):
    end_user_id: str = Field(..., min_length=1)
    config_id: UUID | None = None


class GenerateSuggestionsRequest(BaseModel):
    end_user_id: str
