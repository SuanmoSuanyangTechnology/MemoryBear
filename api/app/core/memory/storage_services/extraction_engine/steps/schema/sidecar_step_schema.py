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


# ── Metadata extraction (async post-dedup) ──
class MetadataStepInput(BaseModel):
    """Input for MetadataExtractionStep."""

    entity_id: str
    entity_name: str
    descriptions: List[str] = Field(
        default_factory=list,
        description="用户实体的 description 列表（可能由分号分隔拆分而来）",
    )
    existing_metadata: dict = Field(
        default_factory=dict,
        description="Neo4j 中已有的元数据，用于增量去重",
    )


class MetadataStepOutput(BaseModel):
    """Output of MetadataExtractionStep."""

    core_facts: List[str] = Field(default_factory=list)
    traits: List[str] = Field(default_factory=list)
    relations: List[str] = Field(default_factory=list)
    goals: List[str] = Field(default_factory=list)
    interests: List[str] = Field(default_factory=list)
    beliefs_or_stances: List[str] = Field(default_factory=list)
    anchors: List[str] = Field(default_factory=list)
    events: List[str] = Field(default_factory=list)

    def has_any(self) -> bool:
        """是否提取到了任何元数据。"""
        return any([
            self.core_facts, self.traits, self.relations, self.goals,
            self.interests, self.beliefs_or_stances, self.anchors, self.events,
        ])
