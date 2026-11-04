"""Minimal read-only mappings of API-owned display projections."""

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class MemoryDisplayRecord(ReadOnlyBase):
    __tablename__ = "memory_display_records"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    operation = Column(String(16), nullable=False)


class MemoryEngineDisplayEvent(ReadOnlyBase):
    __tablename__ = "memory_engine_display_records"

    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    engine_type = Column(String(32), nullable=False)
