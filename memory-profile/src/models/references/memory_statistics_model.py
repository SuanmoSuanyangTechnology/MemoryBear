"""Read-only mappings for API-owned memory statistics tables."""

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class Conversation(ReadOnlyBase):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True)
    app_id = Column(UUID(as_uuid=True), nullable=True)
    user_id = Column(String, nullable=True)
    write_cursor = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=True)


class MemoryMessage(ReadOnlyBase):
    __tablename__ = "memory_messages"

    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(String, nullable=False)
    source = Column(String(32), nullable=False)
    message_seq = Column(Integer, nullable=False)


class MemoryPerceptual(ReadOnlyBase):
    __tablename__ = "memory_perceptual"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
    perceptual_type = Column(Integer, nullable=False)
    storage_service = Column(Integer, nullable=True)
    file_path = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_ext = Column(String, nullable=False)
    summary = Column(String, nullable=True)
    meta_data = Column(JSONB, nullable=True)
    created_time = Column(DateTime, nullable=True)


class ShortTermMemory(ReadOnlyBase):
    __tablename__ = "memory_short_term"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)


class ForgetAudit(ReadOnlyBase):
    __tablename__ = "memory_forget_log"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)
