"""Read-only mapping for the API-owned workspace membership table."""

from sqlalchemy import Boolean, Column
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class WorkspaceMember(ReadOnlyBase):
    __tablename__ = "workspace_members"

    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(Boolean, nullable=True)
