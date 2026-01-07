"""Implicit Memory Schemas

This module defines the Pydantic schemas for the implicit memory system API.
"""

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfidenceLevel(str, Enum):
    """Confidence levels for analysis results."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FrequencyPattern(str, Enum):
    """Frequency patterns for habits."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SEASONAL = "seasonal"
    OCCASIONAL = "occasional"
    EVENT_TRIGGERED = "event_triggered"


# Request Schemas

class TimeRange(BaseModel):
    """Time range for analysis."""
    start_date: datetime.datetime
    end_date: datetime.datetime

    @field_validator('end_date')
    @classmethod
    def end_date_must_be_after_start_date(cls, v, info):
        if 'start_date' in info.data and v <= info.data['start_date']:
            raise ValueError('end_date must be after start_date')
        return v


class DateRange(BaseModel):
    """Date range for filtering."""
    start_date: Optional[datetime.datetime] = None
    end_date: Optional[datetime.datetime] = None

    @field_validator('end_date')
    @classmethod
    def end_date_must_be_after_start_date(cls, v, info):
        if v and 'start_date' in info.data and info.data['start_date'] and v <= info.data['start_date']:
            raise ValueError('end_date must be after start_date')
        return v


class AnalysisConfig(BaseModel):
    """Configuration for analysis operations."""
    llm_model_id: Optional[str] = None
    batch_size: int = 100
    confidence_threshold: float = 0.5
    include_historical_trends: bool = False
    max_conversations: Optional[int] = None


# Response Schemas

class PreferenceTagResponse(BaseModel):
    """A user preference tag with detailed context."""
    model_config = ConfigDict(from_attributes=True)
    
    tag_name: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    supporting_evidence: List[str]
    context_details: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    conversation_references: List[str]
    category: Optional[str] = None


class DimensionScoreResponse(BaseModel):
    """Score for a personality dimension."""
    model_config = ConfigDict(from_attributes=True)
    
    dimension_name: str
    percentage: float = Field(ge=0.0, le=100.0)
    evidence: List[str]
    reasoning: str
    confidence_level: ConfidenceLevel


class DimensionPortraitResponse(BaseModel):
    """Four-dimension personality portrait."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str
    creativity: DimensionScoreResponse
    aesthetic: DimensionScoreResponse
    technology: DimensionScoreResponse
    literature: DimensionScoreResponse
    analysis_timestamp: datetime.datetime
    total_summaries_analyzed: int
    historical_trends: Optional[List[Dict[str, Any]]] = None


class InterestCategoryResponse(BaseModel):
    """Interest category with percentage and evidence."""
    model_config = ConfigDict(from_attributes=True)
    
    category_name: str
    percentage: float = Field(ge=0.0, le=100.0)
    evidence: List[str]
    trending_direction: Optional[str] = None


class InterestAreaDistributionResponse(BaseModel):
    """Distribution of user interests across four areas."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str
    tech: InterestCategoryResponse
    lifestyle: InterestCategoryResponse
    music: InterestCategoryResponse
    art: InterestCategoryResponse
    analysis_timestamp: datetime.datetime
    total_summaries_analyzed: int
    
    @property
    def total_percentage(self) -> float:
        """Calculate total percentage across all interest areas."""
        return self.tech.percentage + self.lifestyle.percentage + self.music.percentage + self.art.percentage


class BehaviorHabitResponse(BaseModel):
    """A behavioral habit identified from conversations."""
    model_config = ConfigDict(from_attributes=True)
    
    habit_description: str
    frequency_pattern: FrequencyPattern
    time_context: str
    confidence_level: ConfidenceLevel
    supporting_summaries: List[str]
    first_observed: datetime.datetime
    last_observed: datetime.datetime
    is_current: bool = True
    specific_examples: List[str]


class UserProfileResponse(BaseModel):
    """Comprehensive user profile."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str
    preference_tags: List[PreferenceTagResponse]
    dimension_portrait: DimensionPortraitResponse
    interest_area_distribution: InterestAreaDistributionResponse
    behavior_habits: List[BehaviorHabitResponse]
    profile_version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    total_summaries_analyzed: int
    analysis_completeness_score: float = Field(ge=0.0, le=1.0)


# Internal/Business Logic Schemas

class MemorySummary(BaseModel):
    """Memory summary from existing storage system."""
    model_config = ConfigDict(from_attributes=True)
    
    summary_id: str
    content: str
    timestamp: datetime.datetime
    participants: List[str]
    summary_type: str


class UserMemorySummary(BaseModel):
    """Memory summary filtered for specific user content."""
    model_config = ConfigDict(from_attributes=True)
    
    summary_id: str
    user_id: str
    user_content: str
    timestamp: datetime.datetime
    confidence_score: float = Field(ge=0.0, le=1.0)
    summary_type: str


class SummaryAnalysisResult(BaseModel):
    """Result of analyzing memory summaries."""
    model_config = ConfigDict(from_attributes=True)
    
    user_id: str
    preferences: List[PreferenceTagResponse]
    dimension_evidence: Dict[str, List[str]]
    interest_evidence: Dict[str, List[str]]
    habit_evidence: List[Dict[str, Any]]
    analysis_timestamp: datetime.datetime
    summaries_analyzed: List[str]


# Aliases for backward compatibility with existing code
PreferenceTag = PreferenceTagResponse
DimensionScore = DimensionScoreResponse
DimensionPortrait = DimensionPortraitResponse
InterestCategory = InterestCategoryResponse
InterestAreaDistribution = InterestAreaDistributionResponse
BehaviorHabit = BehaviorHabitResponse
UserProfile = UserProfileResponse
