"""Read-only mappings for API-owned memory statistics tables."""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class Conversation(ReadOnlyBase):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True)
    app_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=True)
    user_id = Column(String, nullable=True)
    title = Column(String(255), nullable=True)
    write_cursor = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class Message(ReadOnlyBase):
    """Read-only projection of the ``messages`` table (T02)."""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    meta_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True)
    version = Column(Integer, nullable=True)
    is_current = Column(Boolean, nullable=True)
    is_deleted = Column(Boolean, nullable=True)
    parent_message_id = Column(UUID(as_uuid=True), nullable=True)


class MemoryMessage(ReadOnlyBase):
    __tablename__ = "memory_messages"

    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    end_user_id = Column(String, nullable=False)
    source = Column(String(32), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    message_seq = Column(Integer, nullable=False)
    dialog_at = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=True)


class MemoryPerceptual(ReadOnlyBase):
    __tablename__ = "memory_perceptual"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=True)
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
    messages = Column(Text, nullable=False)
    aimessages = Column(Text, nullable=True)
    retrieved_content = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False)


class ForgetAudit(ReadOnlyBase):
    __tablename__ = "memory_forget_log"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(UUID(as_uuid=True), nullable=False)


class ImplicitEmotionsStorage(ReadOnlyBase):
    """Read-only access to API-owned cached implicit profiles."""

    __tablename__ = "implicit_emotions_storage"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    implicit_profile = Column(JSONB, nullable=True)

    updated_at = Column(DateTime, nullable=False)
