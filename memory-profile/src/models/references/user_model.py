"""Read-only mapping of the user fields needed for workspace authorization."""

from sqlalchemy import Boolean, Column
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class User(ReadOnlyBase):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=False)
    is_superuser = Column(Boolean, nullable=False)
