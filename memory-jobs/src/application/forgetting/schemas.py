"""Validate model output before persistence."""

from pydantic import BaseModel, Field


class FusionSummary(BaseModel):
    content: str = Field(min_length=1)
    title: str = "未命名"
    episodic_type: str = "conversation"
