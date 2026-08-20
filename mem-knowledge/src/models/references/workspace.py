"""Read-only Workspace projection used by Knowledge interfaces."""

import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from .base import ReferenceBase


class Workspace(ReferenceBase):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String, index=True, nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    embedding = Column(String, nullable=True)
    rerank = Column(String, nullable=True)
    llm = Column(String, nullable=True)
    vision = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
