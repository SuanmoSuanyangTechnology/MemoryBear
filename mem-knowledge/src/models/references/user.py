"""Read-only User projection used for display-only lookups."""

import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from .base import ReferenceBase


class User(ReferenceBase):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String, index=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    current_workspace_id = Column(UUID(as_uuid=True), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
