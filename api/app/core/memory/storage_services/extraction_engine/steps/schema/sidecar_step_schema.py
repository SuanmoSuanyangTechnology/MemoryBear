"""Pydantic models for hot-pluggable sidecar step inputs and outputs.

Sidecar steps are non-critical (is_critical=False) modules registered via
``@SidecarStepFactory.register`` that run concurrently alongside the main
extraction pipeline.  Failures degrade gracefully to default outputs.
"""

from typing import List

from pydantic import BaseModel, Field


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
