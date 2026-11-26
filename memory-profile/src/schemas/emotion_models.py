"""Emotion extraction models for LLM structured output.

This module contains Pydantic models for emotion extraction from statements,
designed to be used with LLM structured output capabilities.

Classes:
    EmotionExtraction: Model for emotion extraction results from statements
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


class EmotionExtraction(BaseModel):
    """Emotion extraction result model for LLM structured output.
    
    This model represents the structured emotion information extracted from
    a statement using LLM. It includes emotion type, intensity, keywords,
    subject classification, and optional target.
    
    Attributes:
        emotion_type: Type of emotion (joy/sadness/anger/fear/surprise/neutral)
        emotion_intensity: Intensity of emotion (0.0-1.0)
        emotion_keywords: List of emotion keywords from the statement (max 3)
        emotion_subject: Subject of emotion (self/other/object)
        emotion_target: Optional target of emotion (person or object name)
    """
    
    emotion_type: str = Field(
        ..., 
        description="Emotion type: joy/sadness/anger/fear/surprise/neutral"
    )
    emotion_intensity: float = Field(
        ..., 
        ge=0.0, 
        le=1.0,
        description="Emotion intensity from 0.0 to 1.0"
    )
    emotion_keywords: List[str] = Field(
        default_factory=list,
        description="Emotion keywords extracted from the statement (max 3)"
    )
    emotion_subject: str = Field(
        ...,
        description="Emotion subject: self/other/object"
    )
    emotion_target: Optional[str] = Field(
        None,
        description="Emotion target: person or object name"
    )
    
    @field_validator('emotion_type')
    @classmethod
    def validate_emotion_type(cls, v):
        """Validate emotion type is one of the valid values."""
        valid_types = ['joy', 'sadness', 'anger', 'fear', 'surprise', 'neutral']
        if v not in valid_types:
            raise ValueError(f"emotion_type must be one of {valid_types}, got {v}")
        return v
    
    @field_validator('emotion_subject')
    @classmethod
    def validate_emotion_subject(cls, v):
        """Validate emotion subject is one of the valid values."""
        valid_subjects = ['self', 'other', 'object']
        if v not in valid_subjects:
            raise ValueError(f"emotion_subject must be one of {valid_subjects}, got {v}")
        return v
    
    @field_validator('emotion_keywords')
    @classmethod
    def validate_emotion_keywords(cls, v):
        """Validate and limit emotion keywords to max 3 items."""
        if not isinstance(v, list):
            return []
        # Limit to max 3 keywords
        return v[:3]
    
    @field_validator('emotion_intensity')
    @classmethod
    def validate_emotion_intensity(cls, v):
        """Validate emotion intensity is within valid range."""
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"emotion_intensity must be between 0.0 and 1.0, got {v}")
        return v
