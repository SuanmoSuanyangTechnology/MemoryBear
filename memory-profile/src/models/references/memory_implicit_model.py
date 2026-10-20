"""Read-only mapping for the API-owned implicit memory profile table."""

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.models.base import ReadOnlyBase


class ImplicitEmotionsStorage(ReadOnlyBase):
    __tablename__ = "implicit_emotions_storage"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    implicit_profile = Column(JSONB, nullable=True)
    updated_at = Column(DateTime, nullable=True)
