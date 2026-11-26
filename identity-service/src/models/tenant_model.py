import uuid

from sqlalchemy import Boolean, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from src.models.base import SharedBase, utcnow_naive


class Tenants(SharedBase):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    is_active = Column(Boolean, default=True)
    external_id = Column(String(100), nullable=True)
    external_source = Column(String(50), nullable=True)
    default_language = Column(String(10), nullable=False, default="zh", server_default="zh")
    supported_languages = Column(
        ARRAY(String(10)),
        nullable=False,
        default=lambda: ["zh", "en"],
        server_default=text("'{zh,en}'"),
    )
    contact_name = Column(String(100), nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    status = Column(String(50), nullable=True, default="active", server_default="active")
