"""Read-only mapping for the API-owned workspace table."""

from sqlalchemy import Boolean, Column
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class Workspace(ReadOnlyBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True)
    is_active = Column(Boolean, nullable=True)
