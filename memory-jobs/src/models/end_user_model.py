"""Host-owned end-user tables."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import Base


class EndUser(Base):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    other_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False)
    write_time = Column(DateTime, nullable=True)
    memory_insight = Column(String, nullable=True)
    behavior_pattern = Column(String, nullable=True)
    key_findings = Column(String, nullable=True)
    growth_trajectory = Column(String, nullable=True)
    memory_insight_updated_at = Column(DateTime, nullable=True)
    user_summary = Column(String, nullable=True)
    personality_traits = Column(String, nullable=True)
    core_values = Column(String, nullable=True)
    one_sentence_summary = Column(String, nullable=True)
    user_summary_updated_at = Column(DateTime, nullable=True)


class EndUserMerge(Base):
    __tablename__ = "end_user_merge"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    origin_id = Column(UUID(as_uuid=True), nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=False)
