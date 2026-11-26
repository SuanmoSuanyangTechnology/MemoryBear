"""Mapping for the shared ``memory_storage_outbox_events`` table.

DDL is owned by the API's Alembic chain; memory-profile only inserts events so
memory-jobs' outbox projection can mirror the graph change into Elasticsearch.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Identity, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class OutboxEvent(ReadOnlyBase):
    __tablename__ = "memory_storage_outbox_events"

    id = Column(UUID(as_uuid=True), primary_key=True)
    sequence = Column(BigInteger, Identity(), nullable=False)
    label = Column(String(64), nullable=False)
    node_id = Column(Text, nullable=False)
    operation = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    attempt_count = Column(SmallInteger, nullable=False)
    locked_by = Column(String(128), nullable=True)
    claim_token = Column(UUID(as_uuid=True), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)


__all__ = ["OutboxEvent"]