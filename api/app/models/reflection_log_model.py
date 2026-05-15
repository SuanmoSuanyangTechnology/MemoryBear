import datetime
import uuid

from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from app.db import Base


class MemoryReflectionLog(Base):
    """反思引擎操作日志"""
    __tablename__ = "memory_reflection_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    end_user_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    sub_problem = Column(String(32), nullable=False)
    trigger_type = Column(String(16), nullable=False)
    baseline = Column(String(16), nullable=True)
    strategy = Column(String(32), nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="resolved")
    summary_text = Column(String(256), nullable=True)

    entity_ids = Column(ARRAY(Text), nullable=True)
    statement_ids = Column(ARRAY(Text), nullable=True)

    trigger_detail = Column(JSONB, nullable=True)
    solution_detail = Column(JSONB, nullable=True)
    execution_detail = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.datetime.now, nullable=False)