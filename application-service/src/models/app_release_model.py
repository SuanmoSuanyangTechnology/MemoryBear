import uuid
from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from .base import ServiceBase as Base
from ..utils.datetime_utils import utcnow_naive


class AppRelease(Base):
    __tablename__ = "app_releases"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_app_release_app_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    app_id = Column(UUID(as_uuid=True), ForeignKey("apps.id"), nullable=False, index=True)

    # Version number (incremented per app)
    version = Column(Integer, nullable=False, default=1, index=True)
    # Version number for display
    version_name = Column(String, nullable=False)
    # Release notes
    release_notes = Column(String, nullable=True, comment="版本说明")

    # Base-info snapshot (frozen at publish time)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    icon = Column(String, nullable=True)
    icon_type = Column(String, nullable=True)
    type = Column(String, nullable=False)
    visibility = Column(String, default="private")

    # Type-specific config snapshot (stored uniformly for agent/workflow, etc.)
    config = Column(JSON, default=dict)

    # Indexed field for easier queries (e.g. the agent's default model)
    default_model_config_id = Column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True, index=True)

    # Publish info
    published_by = Column(UUID(as_uuid=True), nullable=False, comment="users.id")
    published_at = Column(DateTime, default=utcnow_naive)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

    # Relationship: explicit foreign key to avoid ambiguity with App.current_release_id
    app = relationship("App", back_populates="releases", foreign_keys=[app_id])
    
    # Publisher relationship - explicit join condition via primaryjoin
    # Cross-domain relationship omitted: target model is host-owned and not part of this service's model set (FK column retained).
    
    # Cross-domain property omitted: reads the removed publisher relationship,
    # whose target model (User) is host-owned and not part of this service's model set.

    def __repr__(self):
        return f"<AppRelease(id={self.id}, app_id={self.app_id}, version={self.version})>"
