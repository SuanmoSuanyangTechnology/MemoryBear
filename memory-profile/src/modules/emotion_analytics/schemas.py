"""Emotion analytics HTTP DTOs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.common.normalization import as_utc_naive


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


class EmotionSyncRequest(BaseModel):
    end_user_id: UUID
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        try:
            self.start_time = as_utc_naive(self.start_time)
            self.end_time = as_utc_naive(self.end_time)
        except OverflowError as exc:
            raise ValueError("同步时间超出 UTC 可表示范围") from exc

        if self.start_time >= self.end_time:
            raise ValueError("start_time 必须早于 end_time")
        if self.end_time - self.start_time > timedelta(days=7):
            raise ValueError("同步时间窗口不能超过 7 天")
        return self
