"""Mirrors of the end-user tables (D13: two legacy write paths only).

Written from identity: ``end_users.grace_period_until`` (retention grace
marking) and the MCP get-or-create path (``end_users`` + ``end_user_info``);
``end_user_merge`` is read by that same path. Everything else is read-only
usage. Columns mirror the monolith minus FKs, indexes and relationships.
"""
import uuid

from sqlalchemy import ARRAY, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import SharedBase, utcnow_naive


class EndUser(SharedBase):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    app_id = Column(UUID(as_uuid=True), nullable=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    other_id = Column(String, nullable=True)
    other_name = Column(String, default="", nullable=False)
    other_address = Column(String, default="", nullable=False)
    channel_source = Column(String, nullable=True)
    identity_features = Column(String, nullable=True)
    identity_status = Column(
        String, nullable=False, default="temporary", server_default="temporary"
    )
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    reflection_time = Column(DateTime, nullable=True)
    write_time = Column(DateTime, nullable=True)
    grace_period_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    position = Column(String, nullable=True)
    department = Column(String, nullable=True)
    contact = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    hire_date = Column(DateTime, nullable=True)
    updatetime_profile = Column(DateTime, nullable=True)
    memory_config_id = Column(UUID(as_uuid=True), nullable=True)
    memory_count = Column(Integer, nullable=False, default=0, server_default="0")
    user_summary = Column(Text, nullable=True)
    personality_traits = Column(Text, nullable=True)
    core_values = Column(Text, nullable=True)
    one_sentence_summary = Column(Text, nullable=True)
    user_summary_updated_at = Column(DateTime, nullable=True)
    memory_tags = Column(JSONB, nullable=True)
    memory_tags_updated_at = Column(DateTime, nullable=True)
    memory_tags_source_fingerprint = Column(String(64), nullable=True)
    memory_insight = Column(Text, nullable=True)
    behavior_pattern = Column(Text, nullable=True)
    key_findings = Column(Text, nullable=True)
    growth_trajectory = Column(Text, nullable=True)
    memory_insight_updated_at = Column(DateTime, nullable=True)
    rag_tags = Column(Text, nullable=True)
    rag_personas = Column(Text, nullable=True)
    rag_summary_updated_at = Column(DateTime, nullable=True)


class EndUserMerge(SharedBase):
    __tablename__ = "end_user_merge"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    origin_id = Column(UUID(as_uuid=True), nullable=True)
    origin_other_id = Column(String, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)


class EndUserInfo(SharedBase):
    __tablename__ = "end_user_info"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    other_name = Column(String, nullable=False)
    aliases = Column(ARRAY(String), nullable=True)
    meta_data = Column(JSONB, nullable=True)
    meta_data_new = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
