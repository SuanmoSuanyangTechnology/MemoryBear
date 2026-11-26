"""Mappings for API-owned implicit emotions storage tables."""

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.common.datetime_utils import utcnow_naive
from src.models.base import ReadOnlyBase


class ImplicitEmotionsStorage(ReadOnlyBase):
    """Read-only access to API-owned cached implicit profiles and suggestions."""

    __tablename__ = "implicit_emotions_storage"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    implicit_profile = Column(JSONB, nullable=True)
    emotion_suggestions = Column(JSONB, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive, comment="创建时间")
    implicit_generated_at = Column(DateTime, nullable=True, comment="隐性记忆画像生成时间")

    updated_at = Column(DateTime, nullable=False)
    emotion_generated_at = Column(DateTime, nullable=True)

