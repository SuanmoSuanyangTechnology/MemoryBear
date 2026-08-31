"""Knowledge sharing model copied from the legacy API service."""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from ...db import KnowledgeBase
from ...utils.datetime_utils import utcnow_naive


class KnowledgeShare(KnowledgeBase):
    __tablename__ = "knowledge_shares"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    source_kb_id = Column(UUID(as_uuid=True), nullable=False, comment="source knowledges.id")
    source_workspace_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        comment="source workspaces.id",
    )
    target_kb_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledges.id"),
        nullable=False,
        comment="target knowledges.id",
    )
    target_workspace_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        comment="target workspaces.id",
    )
    shared_by = Column(UUID(as_uuid=True), nullable=False, comment="shared users.id")
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive)

    target_kb = relationship("Knowledge", backref="target_kb")
