"""Read-only User projection used for display-only lookups."""

import uuid

from sqlalchemy import Boolean, Column, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID

from ...utils.datetime_utils import utcnow_naive
from .base import ReferenceBase


class User(ReferenceBase):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    last_login_at = Column(DateTime, nullable=True)
    phone = Column(String(50), nullable=True)
    preferred_language = Column(
        String(10),
        server_default=text("'zh'"),
        default="zh",
        nullable=False,
        index=True,
    )
    current_workspace_id = Column(UUID(as_uuid=True), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
