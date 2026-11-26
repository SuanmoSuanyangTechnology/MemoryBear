"""Mappings for API-owned conversation tables."""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class Conversation(ReadOnlyBase):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True)
    app_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
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



class ConversationDetail(ReadOnlyBase):
    """Read-only projection of the ``conversation_details`` table."""

    __tablename__ = "conversation_details"

    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    theme = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    takeaways = Column(JSON, nullable=True)
    question = Column(JSON, nullable=True)
    info_score = Column(Integer, nullable=True)

