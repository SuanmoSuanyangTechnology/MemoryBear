"""Read-only mappings for API-owned end-user tables."""

from sqlalchemy import Boolean, Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class EndUser(ReadOnlyBase):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    other_name = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False)
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
    memory_tags = Column(JSONB, nullable=True)


class EndUserMerge(ReadOnlyBase):
    __tablename__ = "end_user_merge"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    origin_id = Column(UUID(as_uuid=True), nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=False)
