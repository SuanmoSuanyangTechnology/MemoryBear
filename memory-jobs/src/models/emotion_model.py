"""Host-owned saved emotion suggestions table."""

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import Base


class EmotionStorage(Base):
    __tablename__ = "implicit_emotions_storage"
    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    emotion_suggestions = Column(JSONB, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    emotion_generated_at = Column(DateTime, nullable=True)
