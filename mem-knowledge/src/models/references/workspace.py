"""Read-only Workspace projection used by Knowledge interfaces."""

import uuid

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID

from ...utils.datetime_utils import utcnow_naive
from .base import ReferenceBase


class Workspace(ReferenceBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, index=True, nullable=False)
    icon = Column(String, nullable=True)
    iconType = Column(String, nullable=True)
    description = Column(String, nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    storage_type = Column(String, nullable=True)
    llm = Column(String, nullable=True)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    audio = Column(String, nullable=True)
    video = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    is_default_config = Column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    is_active = Column(Boolean, default=True)
