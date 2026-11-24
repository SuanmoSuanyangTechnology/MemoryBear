import uuid

from sqlalchemy import Boolean, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import SharedBase, utcnow_naive


class User(SharedBase):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, nullable=False)
    email = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    last_login_at = Column(DateTime, nullable=True)
    external_id = Column(String(100), nullable=True)
    external_source = Column(String(50), nullable=True)
    phone = Column(String(50), nullable=True)
    preferred_language = Column(
        String(10), server_default=text("'zh'"), default="zh", nullable=False
    )
    current_workspace_id = Column(UUID(as_uuid=True), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
