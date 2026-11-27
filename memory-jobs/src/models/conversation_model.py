"""Minimal host-owned conversation tables; no schema ownership or DDL."""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False)
    updated_at = Column(DateTime, nullable=True)


class ConversationDetail(Base):
    __tablename__ = "conversation_details"
    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    theme = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    question = Column(JSON, nullable=True)
    takeaways = Column(JSON, nullable=True)
    info_score = Column(Integer, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    meta_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=True)
    is_current = Column(Boolean, nullable=True)
    is_deleted = Column(Boolean, nullable=True)
