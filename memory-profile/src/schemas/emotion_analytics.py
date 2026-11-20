"""Stable emotion-analysis response contracts; saved suggestions stay extensible."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class EmotionTag(BaseModel):
    emotion_type: str
    count: int
    percentage: float
    avg_intensity: float


class EmotionTagsData(BaseModel):
    tags: list[EmotionTag]
    total_count: int
    time_range: dict[str, str] | None


class EmotionKeyword(BaseModel):
    keyword: str
    frequency: int
    emotion_type: str
    avg_intensity: float


class EmotionWordcloudData(BaseModel):
    keywords: list[EmotionKeyword]
    total_keywords: int


class PositivityRate(BaseModel):
    score: float | None
    positive_count: int
    negative_count: int
    neutral_count: int


class Stability(BaseModel):
    score: float | None
    std_deviation: float


class Resilience(BaseModel):
    score: float | None
    recovery_rate: float


class HealthDimensions(BaseModel):
    positivity_rate: PositivityRate
    stability: Stability
    resilience: Resilience


class EmotionHealthData(BaseModel):
    health_score: float | None
    level: str
    dimensions: HealthDimensions
    emotion_distribution: dict[str, int]
    time_range: str


class DailyEmotion(BaseModel):
    type: str
    display_name: str
    count: int
    percentage: float


class EmotionDailyItem(BaseModel):
    date: str
    dialogue_count: int
    emotions: list[DailyEmotion]
    data_quality: Literal["normal", "too_few"]


class EmotionTimelineItem(EmotionDailyItem):
    summary: str


class OverviewSummary(BaseModel):
    dialogue_count: int
    emotion_type_count: int


class EmotionOverviewData(BaseModel):
    data_quality: Literal["no_data", "one_day", "too_few", "normal"]
    summary: OverviewSummary
    conclusion: dict[str, Any] | None
    items: list[EmotionDailyItem]


class PageInfo(BaseModel):
    page: int
    pagesize: int
    total: int
    hasnext: bool


class EmotionTimelineData(BaseModel):
    page: PageInfo
    items: list[EmotionTimelineItem]
