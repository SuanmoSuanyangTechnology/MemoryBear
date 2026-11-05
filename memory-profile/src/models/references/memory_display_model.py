"""Read-only mappings for API-owned memory display records and engine events."""

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class MemoryDisplayRecord(ReadOnlyBase):
    """Read-only mapping for memory_display_records."""

    __tablename__ = "memory_display_records"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    operation = Column(String(16), nullable=False)
    memory_id = Column(String(255), nullable=True)
    memory_type = Column(String(32), nullable=True)
    name = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    score = Column(Float, nullable=True)
    rank = Column(Integer, nullable=True)
    search_mode = Column(String(16), nullable=True)
    query = Column(Text, nullable=True)
    occurred_at = Column(DateTime, nullable=False)


class MemoryEngineDisplayEvent(ReadOnlyBase):
    """Read-only mapping for memory_engine_display_records."""

    __tablename__ = "memory_engine_display_records"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    operation_id = Column(UUID(as_uuid=True), nullable=False)
    engine_type = Column(String(32), nullable=False)
    details = Column(JSONB, nullable=False)
    occurred_at = Column(DateTime, nullable=False)
