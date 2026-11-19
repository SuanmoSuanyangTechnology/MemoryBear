"""Read-only projection of API-owned conversation summaries."""

from sqlalchemy import JSON, Column, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class ConversationDetail(ReadOnlyBase):
    __tablename__ = "conversation_details"

    id = Column(UUID(as_uuid=True), primary_key=True)
    conversation_id = Column(UUID(as_uuid=True), nullable=True)
    theme = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    question = Column(JSON, nullable=True)
    takeaways = Column(JSON, nullable=True)
    info_score = Column(Integer, nullable=True)
