import datetime
import uuid
from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
from sqlalchemy.orm import relationship


class KnowledgeShare(Base):
    __tablename__ = "knowledge_shares"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    source_kb_id = Column(UUID(as_uuid=True), nullable=False, comment="source knowledges.id")
    source_workspace_id = Column(UUID(as_uuid=True), nullable=False, comment="source workspaces.id")
    target_kb_id = Column(UUID(as_uuid=True), ForeignKey('knowledges.id'), nullable=False, comment="target knowledges.id")
    target_workspace_id = Column(UUID(as_uuid=True), ForeignKey('workspaces.id'), nullable=False, comment="target workspaces.id")
    shared_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, comment="shared users.id")
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now)

    # Relationships
    target_kb = relationship("Knowledge", backref="target_kb")
    target_workspace = relationship("Workspace", backref="target_workspace")
    shared_user = relationship("User", backref="shared_user")
