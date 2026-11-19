"""Host-owned forgetting audit and cycle history tables."""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import Base


class ForgetAudit(Base):
    __tablename__ = "memory_forget_log"
    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    node_id = Column(String, nullable=False)
    node_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    trigger = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    recoverable = Column(Boolean, nullable=False)
    operator = Column(UUID(as_uuid=True), nullable=True)
    delete_at = Column(DateTime, nullable=False)
    is_recovered = Column(Boolean, nullable=False)

class ForgetHistory(Base):
    __tablename__ = "forgetting_cycle_history"
    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    execution_time = Column(DateTime, nullable=False)
    merged_count = Column(Integer, nullable=False)
    failed_count = Column(Integer, nullable=False)
    average_activation_value = Column(Float, nullable=True)
    total_nodes = Column(Integer, nullable=False)
    low_activation_nodes = Column(Integer, nullable=False)
    duration_seconds = Column(Float, nullable=True)
    trigger_type = Column(String(50), nullable=False)
