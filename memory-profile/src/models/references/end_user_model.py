"""Read-only mappings for API-owned end-user tables."""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class EndUser(ReadOnlyBase):
    __tablename__ = "end_users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    app_id = Column(UUID(as_uuid=True), nullable=True)
    memory_config_id = Column(UUID(as_uuid=True), nullable=True)
    #: The caller's own user id (``other_id`` on the API model) — the re-embed list shows it.
    other_id = Column(String, nullable=True)
    other_name = Column(String, nullable=False)
    other_address = Column(String, nullable=True)
    #: ``temporary`` / ``confirmed`` / ``expired``; retention counts only temporary users.
    identity_status = Column(String, nullable=False)
    identity_features = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False)
    write_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    reflection_time = Column(DateTime, nullable=True)
    #: Grace deadline written when a shorter retention period expires identities.
    grace_period_until = Column(DateTime, nullable=True)
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
    #: RAG-mode profile caches (``user_summary`` is shared with the graph mode).
    rag_tags = Column(String, nullable=True)
    rag_personas = Column(String, nullable=True)
    rag_summary_updated_at = Column(DateTime, nullable=True)
    #: Cached Neo4j memory-node count the API maintenance job also rewrites;
    #: the migrated forgetting cycle keeps it in sync after a run.
    memory_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        index=True,
        comment="记忆节点总数",
    )


class EndUserMerge(ReadOnlyBase):
    __tablename__ = "end_user_merge"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    origin_id = Column(UUID(as_uuid=True), nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=False)
