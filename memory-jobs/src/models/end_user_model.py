"""Host-owned end-user tables."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import Base


class EndUser(Base):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    other_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False)
    memory_count = Column(Integer, nullable=False)
    write_time = Column(DateTime, nullable=True)
    memory_insight = Column(Text, nullable=True)
    behavior_pattern = Column(Text, nullable=True)
    key_findings = Column(Text, nullable=True)
    growth_trajectory = Column(Text, nullable=True)
    memory_insight_updated_at = Column(DateTime, nullable=True)
    user_summary = Column(Text, nullable=True)
    personality_traits = Column(Text, nullable=True)
    core_values = Column(Text, nullable=True)
    one_sentence_summary = Column(Text, nullable=True)
    user_summary_updated_at = Column(DateTime, nullable=True)


class EndUserMerge(Base):
    __tablename__ = "end_user_merge"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    origin_id = Column(UUID(as_uuid=True), nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=False)
