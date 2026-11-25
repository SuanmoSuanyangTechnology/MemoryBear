"""Value models for the migrated implicit-profile generation flow."""

import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from src.common.timing import to_timestamp_ms


class FrequencyPattern(str, Enum):  # noqa: UP042 - Preserve the legacy str() behavior.
    """Frequency patterns for habits."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SEASONAL = "seasonal"
    OCCASIONAL = "occasional"
    EVENT_TRIGGERED = "event_triggered"


class TimeRange(BaseModel):
    """Time range for analysis."""

    start_date: datetime.datetime
    end_date: datetime.datetime

    @field_validator("end_date")
    @classmethod
    def end_date_must_be_after_start_date(cls, v, info):
        if "start_date" in info.data and v <= info.data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v

    @field_serializer("start_date", when_used="json")
    def _serialize_start_date(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("end_date", when_used="json")
    def _serialize_end_date(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class DateRange(BaseModel):
    """Date range for filtering."""

    start_date: datetime.datetime | None = None
    end_date: datetime.datetime | None = None

    @field_validator("end_date")
    @classmethod
    def end_date_must_be_after_start_date(cls, v, info):
        if (
            v
            and "start_date" in info.data
            and info.data["start_date"]
            and v <= info.data["start_date"]
        ):
            raise ValueError("end_date must be after start_date")
        return v

    @field_serializer("start_date", when_used="json")
    def _serialize_start_date(self, dt: datetime.datetime | None):
        return to_timestamp_ms(dt)

    @field_serializer("end_date", when_used="json")
    def _serialize_end_date(self, dt: datetime.datetime | None):
        return to_timestamp_ms(dt)


class PreferenceTagResponse(BaseModel):
    """A user preference tag with detailed context."""

    model_config = ConfigDict(from_attributes=True)

    tag_name: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[str]
    context_details: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    conversation_references: list[str]
    category: str | None = None

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class DimensionScoreResponse(BaseModel):
    """Score for a personality dimension."""

    model_config = ConfigDict(from_attributes=True)

    dimension_name: str
    percentage: float = Field(ge=0.0, le=100.0)
    evidence: list[str]
    reasoning: str
    confidence_level: int = Field(ge=0, le=100)


class DimensionPortraitResponse(BaseModel):
    """Four-dimension personality portrait."""

    model_config = ConfigDict(from_attributes=True)

    creativity: DimensionScoreResponse
    aesthetic: DimensionScoreResponse
    technology: DimensionScoreResponse
    literature: DimensionScoreResponse
    analysis_timestamp: datetime.datetime
    total_summaries_analyzed: int
    historical_trends: list[dict[str, Any]] | None = None

    @field_serializer("analysis_timestamp", when_used="json")
    def _serialize_analysis_timestamp(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class InterestCategoryResponse(BaseModel):
    """Interest category with percentage and evidence."""

    model_config = ConfigDict(from_attributes=True)

    category_name: str
    percentage: float = Field(ge=0.0, le=100.0)
    evidence: list[str]
    trending_direction: str | None = None


class InterestAreaDistributionResponse(BaseModel):
    """Distribution of user interests across four areas."""

    model_config = ConfigDict(from_attributes=True)

    tech: InterestCategoryResponse
    lifestyle: InterestCategoryResponse
    music: InterestCategoryResponse
    art: InterestCategoryResponse
    analysis_timestamp: datetime.datetime
    total_summaries_analyzed: int

    @property
    def total_percentage(self) -> float:
        """Calculate total percentage across all interest areas."""
        return (
            self.tech.percentage
            + self.lifestyle.percentage
            + self.music.percentage
            + self.art.percentage
        )

    @field_serializer("analysis_timestamp", when_used="json")
    def _serialize_analysis_timestamp(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class BehaviorHabitResponse(BaseModel):
    """A behavioral habit identified from conversations."""

    model_config = ConfigDict(from_attributes=True)

    habit_description: str
    frequency_pattern: FrequencyPattern
    time_context: str
    confidence_level: int = Field(ge=0, le=100)
    first_observed: datetime.datetime
    last_observed: datetime.datetime
    is_current: bool = True
    specific_examples: list[str]

    @field_serializer("first_observed", when_used="json")
    def _serialize_first_observed(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("last_observed", when_used="json")
    def _serialize_last_observed(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class MemorySummary(BaseModel):
    """Memory summary from existing storage system."""

    model_config = ConfigDict(from_attributes=True)

    summary_id: str
    content: str
    timestamp: datetime.datetime
    participants: list[str]
    summary_type: str

    @field_serializer("timestamp", when_used="json")
    def _serialize_timestamp(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class UserMemorySummary(BaseModel):
    """Memory summary filtered for specific user content."""

    model_config = ConfigDict(from_attributes=True)

    summary_id: str
    user_content: str
    timestamp: datetime.datetime
    confidence_score: float = Field(ge=0.0, le=1.0)
    summary_type: str

    @field_serializer("timestamp", when_used="json")
    def _serialize_timestamp(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class GenerateProfileRequest(BaseModel):
    """生成完整用户画像请求"""

    end_user_id: str = Field(..., description="终端用户ID")


PreferenceTag = PreferenceTagResponse
DimensionScore = DimensionScoreResponse
DimensionPortrait = DimensionPortraitResponse
InterestCategory = InterestCategoryResponse
InterestAreaDistribution = InterestAreaDistributionResponse
BehaviorHabit = BehaviorHabitResponse
