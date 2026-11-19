"""Validate model output before persistence."""

from pydantic import BaseModel, Field


class EmotionSuggestion(BaseModel):
    type: str
    title: str
    content: str
    priority: str
    actionable_steps: list[str] = Field(min_length=1)


class EmotionSuggestions(BaseModel):
    health_summary: str = Field(min_length=1)
    suggestions: list[EmotionSuggestion] = Field(min_length=3, max_length=5)
