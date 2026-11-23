"""Validate model output before persistence."""

from pydantic import BaseModel


class EmotionSuggestion(BaseModel):
    type: str
    title: str
    content: str
    priority: str
    actionable_steps: list[str]


class EmotionSuggestions(BaseModel):
    health_summary: str
    suggestions: list[EmotionSuggestion]
