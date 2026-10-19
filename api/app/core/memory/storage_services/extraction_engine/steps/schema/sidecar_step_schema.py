"""Pydantic models for hot-pluggable sidecar step inputs and outputs.

Sidecar steps are non-critical (is_critical=False) modules registered via
``@SidecarStepFactory.register`` that run concurrently alongside the main
extraction pipeline.  Failures degrade gracefully to default outputs.
"""

from typing import List

from pydantic import BaseModel, Field, field_validator

from app.core.memory.emotion.emotion_buckets import normalize_emotion_type


# ── Emotion extraction (sidecar) ──
class EmotionStepInput(BaseModel):
    """Input for EmotionExtractionStep."""

    statement_id: str
    statement_text: str
    speaker: str


class EmotionStepOutput(BaseModel):
    """Output of EmotionExtractionStep."""

    emotion_type: str = "neutral"
    emotion_intensity: float = 0.0
    emotion_keywords: List[str] = Field(default_factory=list)

    @field_validator("emotion_type", mode="before")
    @classmethod
    def validate_emotion_type(cls, v):
        """归一化到 10 类；未知值回落为 neutral。"""
        return normalize_emotion_type(v) or "neutral"
