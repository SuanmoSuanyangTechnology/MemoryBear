"""Read-only mapping for the API-owned reflection log table."""

from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from src.models.base import ReadOnlyBase


class MemoryReflectionLog(ReadOnlyBase):
    """Projection of ``memory_reflection_log`` used by the reflection-log reads.

    The DDL is owned by the API host; this mapping only mirrors the columns the
    migrated endpoints return, so no default/relationship is needed.
    """

    __tablename__ = "memory_reflection_log"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    sub_problem = Column(String(32), nullable=False)
    trigger_type = Column(String(16), nullable=False)
    baseline = Column(String(16), nullable=True)
    strategy = Column(String(32), nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(16), nullable=False)
    summary_text = Column(String(256), nullable=True)

    entity_ids = Column(ARRAY(Text), nullable=True)
    statement_ids = Column(ARRAY(Text), nullable=True)

    trigger_detail = Column(JSONB, nullable=True)
    solution_detail = Column(JSONB, nullable=True)
    execution_detail = Column(JSONB, nullable=True)

    created_at = Column(DateTime, nullable=False)