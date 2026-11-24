"""Read-only mapping for the API-owned reflection log table."""

from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from src.models.base import ReadOnlyBase


class MemoryReflectionLog(ReadOnlyBase):
    __tablename__ = "memory_reflection_log"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    sub_problem = Column(String(32), nullable=False)
    trigger_type = Column(String(16), nullable=False)
    baseline = Column(String(16))
    strategy = Column(String(32))
    confidence = Column(Float)
    status = Column(String(16), nullable=False)
    summary_text = Column(String(256))
    entity_ids = Column(ARRAY(Text))
    statement_ids = Column(ARRAY(Text))
    trigger_detail = Column(JSONB)
    solution_detail = Column(JSONB)
    execution_detail = Column(JSONB)
    created_at = Column(DateTime, nullable=False)
